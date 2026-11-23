# Sends one message to zenko-main, keeping conversation history in a JSON file. Usage: python3 -I talk.py HISTORY.json "message"
import json, sys, time, os, urllib.request
hist_path, msg = sys.argv[1], sys.argv[2]
hist = json.load(open(hist_path)) if os.path.exists(hist_path) else []
hist.append({"role": "user", "content": msg})
body = {"model": "zenko-main", "max_tokens": 8192, "temperature": 0.7, "messages": hist}
t = time.time()
d = json.load(urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:1234/v1/chat/completions",
    json.dumps(body).encode(), {"Content-Type": "application/json"}), timeout=900))
m = d["choices"][0]["message"]; reply = (m.get("content") or "").strip()
hist.append({"role": "assistant", "content": reply}); json.dump(hist, open(hist_path, "w"), indent=1)
print(reply); print(f"\n[{d['usage']['completion_tokens']} tokens, {time.time()-t:.0f}s, finish={d['choices'][0]['finish_reason']}]")
