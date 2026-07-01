Fix first (3): #1 format/model mismatch (may not run), #2 autonomous pay/exec no gate, #3 zero injection defense. Rest is cost + correctness + privacy.

┌─────┬─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┬──────┬────────────────────────────────────────────────────────────────────┬──────────────────────────────────────────┐
│  #  │                                                                      Issue                                                                      │ Sev  │                                Fix                                 │             Value if solved              │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────┼────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────┤
│ 1   │ Model/format mismatch. Payload native Anthropic (input_schema tools). deepseek-v4-flash speaks OpenAI-compat (tools[].function.parameters).     │ CRIT │ Confirm translation shim exists, else tools silently drop / call   │ Agent actually works. Tools register. No │
│     │ Also v4-flash not real DeepSeek name (V3/R1/V3.2; "flash"=Gemini).                                                                              │      │ errors. Verify real model id.                                      │  silent no-op.                           │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────┼────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────┤
│ 2   │ High-risk acts, no approval gate. browser explore doc says self-drives, fills full card#/cvc, clicks Pay — never says it asks approval (only    │ CRIT │ Force per-action approval on explore payment/submit + run_command  │ Stops autonomous spend/destructive cmd.  │
│     │ act does). run_command = free-form shell. On cheap model.                                                                                       │      │ writes. Least-privilege.                                           │                                          │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────┼────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────┤
│ 3   │ No prompt-injection defense. Agent eats untrusted email/web/file → holds send_email, secrets, payment, run_command, memory-write. No "treat     │ CRIT │ Add injection guard rail; mark external content untrusted; gate    │ Blocks data exfil + memory poisoning.    │
│     │ tool output as untrusted" rule.                                                                                                                 │      │ exfil tools.                                                       │                                          │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────┼────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────┤
│ 4   │ Sensitive PII every turn to 3rd-party model. Home addr, child name+DOB+photo refs, health (cefuroxime ear infection), phones — to external      │ HIGH │ Minimize/redact; keep special-category (health/child) out of       │ Privacy + legal risk gone.               │
│     │ (likely non-EU) API each turn.                                                                                                                  │      │ remote ctx; check GDPR/host region.                                │                                          │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────┼────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────┤
│ 5   │ task_reflections bloat. Huge, mostly [failure]/[partial], redundant (5× "check allowed list", 4× "secret scope"), repeated every turn.          │ HIGH │ Cap to ~5 positive lessons, dedupe, drop failure-framing.          │ Big token save/turn + less               │
│     │                                                                                                                                                 │      │                                                                    │ hedging/negative bias.                   │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────┼────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────┤
│ 6   │ Allowed-cmd list enforced but never shown. Reflections full of "command not allowed". Model blind.                                              │ HIGH │ List allowed prefixes in prompt.                                   │ Kills the #1 recurring failure.          │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────┼────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────┤
│ 7   │ available_skills in turn-1, gone turn-2. Staleness — their own reflection flags this exact bug.                                                 │ HIGH │ Inject skills index per-turn like memories.                        │ Model keeps skill access mid-convo.      │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────┼────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────┤
│ 8   │ Persona≠tools. "Coding helper" holds email/calendar/whatsapp/payment-browser. Whitelisting (mpa's point) defeated.                              │ HIGH │ Scope coding persona to code tools.                                │ Real least-privilege per persona.        │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────┼────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────┤
│ 9   │ Contradictory run_command rules. "read/query only" vs gh pr create + jobs.py edit via run_command (both writes).                                │ MED  │ Pick one rule; say writes-via-gh/jobs OK.                          │ Less confused tool choice.               │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────┼────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────┤
│ 10  │ Quiet-hours soft. 7PM–7AM no-notify is a rankable memory (could drop). Now 22:41.                                                               │ MED  │ Promote to hard system rule.                                       │ Won't ping at night.                     │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────┼────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────┤
│ 11  │ Tool overlap. run_command vs file-harness vs run_command_in_dir; browser doc duplicated as inline tool + skill. ~21 tools hurt flash selection. │ MED  │ Dedupe; defer heavy browser doc to skill.                          │ Better tool picks + token save.          │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────┼────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────┤
│ 12  │ No temperature/top_p. Tool-arg agent on provider default (maybe 1.0).                                                                           │ MED  │ Set low temp (0–0.3).                                              │ Stable tool args.                        │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────┼────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────┤
│ 13  │ Memory split. Write=raw sqlite3 via run_command, read=recall_memory tool. Raw SQL insert of user text = quoting/injection risk; dupes exist     │ MED  │ One mechanism, parameterized insert.                               │ No SQL break/inject; no dupes.           │
│     │ (Luna ×2) despite dedupe rule.                                                                                                                  │      │                                                                    │                                          │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────┼────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────┤
│ 14  │ No caching + volatile blocks in user turns. Huge static sys uncached; big reflections in user msg defeat reuse.                                 │ MED  │ Cache stable system; keep volatile small.                          │ Lower cost/latency (if                   │
│     │                                                                                                                                                 │      │                                                                    │ Anthropic-compat).                       │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────┼────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────┤
│ 15  │ Data typos. 20216 (Berlin yr), mixed date fmts (11.03.1989 / 05/09/2021 / August 25), Zurich/Zürich.                                            │ LOW  │ Normalize ISO dates; fix typo.                                     │ No date misread.                         │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────┼────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────┤
│ 16  │ Identity muddle. Name "Hopper" = persona "Coding helper" AND separate coding-helper persona; both hopper+coding-helper labeled "Coding helper". │ LOW  │ Distinct names/labels.                                             │ Won't mis-spawn duplicate specialist.    │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────┼────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────┤
│ 17  │ Persona over-promises. "remember owner's stack/conventions" — none stored.                                                                      │ LOW  │ Store stack facts or drop claim.                                   │ No false confidence.                     │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────┼────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────┤
│ 18  │ Hidden limits. generate_image 1/day budget + voice marker rules learned via failure, not in tool desc.                                          │ LOW  │ Put budget/limit in desc.                                          │ Fewer wasted calls.                      │
├─────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────┼────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────┤
│ 19  │ Redundant persona text. <personalia> ≈ <character> (both "smallest change, stdlib over deps").                                                  │ LOW  │ Merge.                                                             │ Minor token save.                        │
└─────┴─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┴──────┴────────────────────────────────────────────────────────────────────┴──────────────────────────────────────────┘

Theme: 3 things repeat — (a) rules referenced but not given (allowed cmds), (b) huge repeated context (reflections/PII/skills) costing tokens + leaking data, (c) powerful tools w/o gates on a cheap model.



1. i did not understand the issue: explain it to me better (also note that deepseek-v4-flash is an actual recent model you dont know about yet)
2. valid point
3. valid point
4. this is an accepted tradeoff - dont address
5. valid point
6. valid point
7. valid point
8. this point will be addressed from the config ui - this is a test agent hence all tools available
9. valid point
10. did not understand this either, explain
11. valid point
12. valid point - maybe this should be a config in the admin ui as well
13. valid point
14. valid point
15. valid point
16. skip
17. skip
18. valid point
19. already addressed
