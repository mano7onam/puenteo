//! puenteo-core — native hot paths for puenteo.
//!
//! The Python package works without this module; when importable it is used
//! for the expensive part of reading agent transcripts: scanning multi-GB
//! JSONL logs where most lines are tool output, reasoning blobs or images we
//! discard anyway. We pre-filter lines on raw bytes and only JSON-decode the
//! ones that can carry a user/assistant message.
//!
//! Returned rows are plain tuples `(role, text, timestamp, src)` so the
//! Python side keeps ownership of noise filtering, dedup and indexing.

use memchr::memmem;
use pyo3::prelude::*;
use rayon::prelude::*;
use serde_json::Value;
use std::fs::File;

type Row = (String, String, String, String);

/// Exact port of `puenteo.util.stringify_content` (keep in sync; tests compare both).
fn text_of(v: &Value) -> String {
    match v {
        Value::Null => String::new(),
        Value::String(s) => s.clone(),
        Value::Array(items) => {
            let mut parts: Vec<String> = Vec::new();
            for it in items {
                match it {
                    Value::String(s) => parts.push(s.clone()),
                    Value::Object(o) => {
                        let bt = o.get("type").and_then(|t| t.as_str());
                        match bt {
                            None | Some("text") | Some("input_text") | Some("output_text") => {
                                if let Some(t) = o.get("text").filter(|t| truthy(t)) {
                                    parts.push(py_str(t));
                                } else if let Some(c) = o.get("content").filter(|c| truthy(c)) {
                                    parts.push(text_of(c));
                                }
                            }
                            Some("thinking") | Some("reasoning") => {}
                            Some("tool_use") => {
                                let name = o.get("name").filter(|n| truthy(n)).map(py_str).unwrap_or_else(|| "tool".into());
                                parts.push(format!("[tool_use:{}]", name));
                            }
                            Some("tool_result") => parts.push("[tool_result]".into()),
                            Some(_) => {
                                if let Some(t) = o.get("text").filter(|t| truthy(t)) {
                                    parts.push(py_str(t));
                                }
                            }
                        }
                    }
                    other => parts.push(py_str(other)),
                }
            }
            parts.retain(|p| !p.is_empty());
            parts.join("\n")
        }
        Value::Object(o) => match o.get("text") {
            Some(t) => if truthy(t) { py_str(t) } else { String::new() },
            None => {
                let s: String = Value::Object(o.clone()).to_string();
                s.chars().take(4000).collect()
            }
        },
        other => py_str(other),
    }
}

/// `json.dumps(v, ensure_ascii=False)` formatting (", " and ": " separators).
fn py_dumps(v: &Value) -> String {
    match v {
        Value::Array(a) => format!("[{}]", a.iter().map(py_dumps).collect::<Vec<_>>().join(", ")),
        Value::Object(o) => format!(
            "{{{}}}",
            o.iter()
                .map(|(k, v)| format!("{}: {}", Value::String(k.clone()), py_dumps(v)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
        other => other.to_string(),
    }
}

fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
    }
}

fn py_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Null => "None".into(),
        other => other.to_string(),
    }
}

/// Does `line` contain `"<key>"\s*:\s*"<value>"`? Byte-level, whitespace tolerant.
fn has_kv(line: &[u8], key: &[u8], value: &memmem::Finder) -> bool {
    let mut start = 0;
    while let Some(pos) = value.find(&line[start..]) {
        let at = start + pos;
        // walk back over whitespace and ':' to the end of the key
        let mut i = at;
        while i > 0 && matches!(line[i - 1], b' ' | b'\t') {
            i -= 1;
        }
        if i > 0 && line[i - 1] == b':' {
            i -= 1;
            while i > 0 && matches!(line[i - 1], b' ' | b'\t') {
                i -= 1;
            }
            if i >= key.len() && &line[i - key.len()..i] == key {
                return true;
            }
        }
        start = at + 1;
    }
    false
}

fn map_file(path: &str) -> std::io::Result<memmap2::Mmap> {
    let f = File::open(path)?;
    // Safety: read-only mapping of a file we don't write; agents append, which is fine.
    unsafe { memmap2::Mmap::map(&f) }
}

fn lines(buf: &[u8]) -> impl Iterator<Item = &[u8]> {
    buf.split(|b| *b == b'\n').filter(|l| !l.is_empty())
}

// ------------------------------------------------------------------ codex

fn codex_rows(buf: &[u8], include_tools: bool) -> Vec<Row> {
    let f_resp = memmem::Finder::new(b"\"response_item\"");
    let f_event = memmem::Finder::new(b"\"event_msg\"");
    let f_meta = memmem::Finder::new(b"\"session_meta\"");
    let f_msg = memmem::Finder::new(b"\"message\"");
    let f_um = memmem::Finder::new(b"user_message");
    let f_am = memmem::Finder::new(b"agent_message");
    let f_call = memmem::Finder::new(b"_call");
    let ty = b"\"type\"";
    let mut out = Vec::new();
    for line in lines(buf) {
        // Cheap byte-level routing: decode only lines that can hold a message.
        // Codex writes {"timestamp":…,"type":…,"payload":{"type":…}} — both types sit near the start.
        let probe = &line[..line.len().min(240)];
        let is_resp = has_kv(probe, ty, &f_resp);
        let is_event = has_kv(probe, ty, &f_event);
        if has_kv(probe, ty, &f_meta) {
            if let Ok(v) = serde_json::from_slice::<Value>(line) {
                let p = &v["payload"];
                let id = p["id"].as_str().or(p["session_id"].as_str()).unwrap_or("");
                let cwd = p["cwd"].as_str().unwrap_or("");
                out.push(("__meta__".into(), cwd.into(), String::new(), id.into()));
            }
            continue;
        }
        let wanted = if is_resp {
            has_kv(probe, ty, &f_msg) || (include_tools && f_call.find(probe).is_some())
        } else if is_event {
            f_um.find(probe).is_some() || f_am.find(probe).is_some()
        } else {
            false
        };
        if !wanted {
            continue;
        }
        let v: Value = match serde_json::from_slice(line) {
            Ok(v) => v,
            Err(_) => continue,
        };
        let ts = v["timestamp"].as_str().unwrap_or("").to_string();
        let p = &v["payload"];
        let ptype = p["type"].as_str().unwrap_or("");
        if is_resp {
            match ptype {
                "message" => {
                    let role = p["role"].as_str().unwrap_or("assistant");
                    let text = text_of(&p["content"]);
                    out.push((role.into(), text, ts, "response_item".into()));
                }
                "function_call" | "custom_tool_call" | "tool_call" if include_tools => {
                    let name = p["name"].as_str().unwrap_or("tool");
                    let args = match &p["arguments"] {
                        Value::String(s) => s.clone(),
                        Value::Null => p["input"].as_str().unwrap_or("").to_string(),
                        other => other.to_string(),
                    };
                    let args: String = args.chars().take(500).collect();
                    out.push(("assistant".into(), format!("[tool_call {}] {}", name, args), ts, "tool".into()));
                }
                "function_call_output" | "custom_tool_call_output" if include_tools => {
                    let o = text_of(&p["output"]);
                    let o: String = o.chars().take(800).collect();
                    out.push(("tool".into(), format!("[tool_result] {}", o), ts, "tool".into()));
                }
                _ => {}
            }
        } else if ptype == "user_message" || ptype == "agent_message" {
            let role = if ptype == "user_message" { "user" } else { "assistant" };
            let msg = if truthy(&p["message"]) { &p["message"] } else { &p["text"] };
            out.push((role.into(), text_of(msg), ts, "event_msg".into()));
        }
    }
    out
}

// ------------------------------------------------------------------ claude

fn claude_rows(buf: &[u8], include_tools: bool) -> Vec<Row> {
    let f_user = memmem::Finder::new(b"\"user\"");
    let f_asst = memmem::Finder::new(b"\"assistant\"");
    let f_title = memmem::Finder::new(b"\"ai-title\"");
    let mut out = Vec::new();
    for line in lines(buf) {
        let ty = b"\"type\"";
        let is_title = has_kv(line, ty, &f_title);
        if !is_title && !has_kv(line, ty, &f_user) && !has_kv(line, ty, &f_asst) {
            continue;
        }
        let v: Value = match serde_json::from_slice(line) {
            Ok(v) => v,
            Err(_) => continue,
        };
        let t = v["type"].as_str().unwrap_or("");
        if t == "ai-title" {
            out.push(("__title__".into(), v["title"].as_str().unwrap_or("").into(), String::new(), String::new()));
            continue;
        }
        if t != "user" && t != "assistant" {
            continue;
        }
        if v["isMeta"].as_bool() == Some(true)
            || v["isCompactSummary"].as_bool() == Some(true)
            || v["isVisibleInTranscriptOnly"].as_bool() == Some(true)
        {
            continue;
        }
        let msg = &v["message"];
        let role = msg["role"].as_str().unwrap_or(t);
        let ts = v["timestamp"].as_str().unwrap_or("").to_string();
        let mid = msg["id"].as_str().unwrap_or("").to_string();
        let mut parts: Vec<String> = Vec::new();
        let mut tools: Vec<String> = Vec::new();
        match &msg["content"] {
            Value::String(s) => parts.push(s.clone()),
            Value::Array(blocks) => {
                for b in blocks {
                    match b["type"].as_str().unwrap_or("") {
                        "text" => parts.push(b["text"].as_str().unwrap_or("").to_string()),
                        "image" => parts.push("[image]".into()),
                        "tool_use" if include_tools => {
                            let inp: String = py_dumps(&b["input"]).chars().take(400).collect();
                            tools.push(format!("[tool_use {}] {}", b["name"].as_str().unwrap_or("tool"), inp));
                        }
                        "tool_result" if include_tools => {
                            let c: String = text_of(&b["content"]).chars().take(600).collect();
                            tools.push(format!("[tool_result] {}", c));
                        }
                        _ => {}
                    }
                }
            }
            _ => {}
        }
        let mut text = parts.join("\n");
        if !tools.is_empty() {
            if !text.is_empty() {
                text.push('\n');
            }
            text.push_str(&tools.join("\n"));
        }
        if text.trim().is_empty() {
            continue;
        }
        let cwd = v["cwd"].as_str().unwrap_or("").to_string();
        let sid = v["sessionId"].as_str().unwrap_or("").to_string();
        // src = "<message id>\t<cwd>\t<session id>" (Python splits it)
        out.push((role.into(), text, ts, format!("{}\t{}\t{}", mid, cwd, sid)));
    }
    out
}

fn run(kind: &str, path: &str, include_tools: bool) -> std::io::Result<Vec<Row>> {
    let m = map_file(path)?;
    Ok(match kind {
        "codex" => codex_rows(&m, include_tools),
        _ => claude_rows(&m, include_tools),
    })
}

#[pyfunction]
#[pyo3(signature = (path, include_tools=false))]
fn extract_codex(py: Python<'_>, path: String, include_tools: bool) -> PyResult<Vec<Row>> {
    py.allow_threads(|| run("codex", &path, include_tools))
        .map_err(|e| pyo3::exceptions::PyOSError::new_err(e.to_string()))
}

#[pyfunction]
#[pyo3(signature = (path, include_tools=false))]
fn extract_claude(py: Python<'_>, path: String, include_tools: bool) -> PyResult<Vec<Row>> {
    py.allow_threads(|| run("claude", &path, include_tools))
        .map_err(|e| pyo3::exceptions::PyOSError::new_err(e.to_string()))
}

/// Parse many files in parallel (all cores). `jobs` = [(kind, path)]; failed files → [].
#[pyfunction]
#[pyo3(signature = (jobs, include_tools=false))]
fn extract_many(py: Python<'_>, jobs: Vec<(String, String)>, include_tools: bool) -> Vec<Vec<Row>> {
    py.allow_threads(|| {
        jobs.par_iter()
            .map(|(k, p)| run(k, p, include_tools).unwrap_or_default())
            .collect()
    })
}

#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_function(wrap_pyfunction!(extract_codex, m)?)?;
    m.add_function(wrap_pyfunction!(extract_claude, m)?)?;
    m.add_function(wrap_pyfunction!(extract_many, m)?)?;
    Ok(())
}
