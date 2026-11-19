# websec — headless web-app authorization testing framework

A Burp-equivalent capability for this (GUI-less) Kali box, driven entirely from
the CLI via Claude Code. Wraps the installed tools and adds a config-driven
authorization engine (the Autorize equivalent).

Run `websec <command> -h` for options.

| Burp component   | websec command | Backing tool            |
|------------------|----------------|-------------------------|
| Autorize (authz) | `websec authz` | built-in engine         |
| Repeater         | `websec req`   | built-in (urllib)       |
| Proxy            | `websec proxy` | mitmdump                |
| Intruder         | `websec fuzz`  | ffuf                    |
| Scanner          | `websec scan`  | nuclei                  |
| Target/crawl     | `websec crawl` | katana                  |
| Subdomains/DNS   | `websec recon` | subfinder + dnsx        |
| Param discovery  | `websec params`| arjun                   |
| Collaborator     | `websec oob`   | interactsh-client       |
| Decoder          | `websec jwt`   | built-in                |

## Authorization matrix (the centerpiece)

Define a profile in `profiles/<name>.json`:
- `base`, `login_path`
- `roles`: each role has either `{"login": {...}}` (tool logs in for a token),
  `{"token": "..."}` (static), or `{}` (anonymous)
- `endpoints`: `{method, path, allow:[roles]}` — `allow` is the *intended* role list

Then:

    websec authz <profile>              # GET/HEAD/OPTIONS only (safe default)
    websec authz <profile> --allow-writes   # also PATCH/POST/DELETE (mutates target!)

Verdicts per cell: green = behaved as intended; `VIOL` (red) = a role that should
be denied got 2xx (broken access control); `!low` (amber) = an allowed role was
blocked. Findings are printed and saved to `loot/authz-<profile>-<ts>.json`.

## Safety
- `authz` never sends mutating methods unless `--allow-writes` is given.
- Only run against targets you are authorized to test.
- Profiles may contain credentials — they are chmod 600.
