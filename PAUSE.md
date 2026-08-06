# PAUSED — Mission 3 (diagnostic-v08), user switching networks

Paused at user request (home network switch). Resume by re-reading this file
and the mission at `/media/cloud/2982-E16B/AetherSparse/DROID_MISSION_3.md`.

## Branch / repo state

- Branch `droid/diagnostic-v08`, repo `/media/cloud/2982-E16B/work/AetherSparse`.
- Last commit: 7f87f1a (phase-6 compat gate @25k). All local results committed.
- Deliverable draft: `reports/droid/V08_DIAGNOSTIC.md` (in progress).

## s600 state (aethera-litellm-s600, /root/work)

RUNNING (systemd units, survive network; results land in
/root/work/AetherSparse/reports/droid/v08/):

- `sem100k` — Phase 4 semantic arms @100k (was pools 400/1280 after batch-bm25
  fix; ETA ~2-3 h from restart)
- `ladderA` — 25k oracle ladder rungs 0,1,2 sequential (was 1375/2050 rung 0/1)
- `ladderB` — 25k ladder rungs 3,4 (was 1000/2050)
- `carry100knone` — fixed 0.35 carry @100k (was 1400/2050)
- `carry100kmargin` — margin-gated carry @100k (just launched)

NOT YET LAUNCHED (resume checklist):

1. `carry100kcompat` — compat-gated carry @100k (mirror of 25k run)
2. Phase 1b @100k scaled sweep: probe-scale 2/4/8 with candidate-limit
   192/384/768 on selector-100k-p3.sqlite (pattern: sweep25ks* units)
3. After ladder @25k lands: dominant-rung confirmation @100k (Phase 3)
4. Phase 2 four-stage decomposition @25k from ladder outcomes
5. Phase 7: final 397k confirmation run ONCE (with carry decision +
   transferred weights; --discourse-boost per Phase 6 outcome)
6. Finish V08_DIAGNOSTIC.md decision label

## Key findings so far (all committed)

- Scaling: 82.73 → 81.17 → 75.39 → 67.42 strict (accelerating pp/decade)
- 1a: weight staleness NOT the cause (25k +0.70 pp, 100k +0.31 pp; gate <2 pp)
- 1b: candidate budget NOT binding @25k (8x probes → +0.47 pp strict;
  ranking loses 10.4 pp with 92% pool recall)
- 1c: erosion = alias/redirect/misspelling trio 53%; multi-source only 17.9%
- Category stage-split @100k: alias/redirect = candidate-pool collapse;
  misspelling = constant ~43% ranking loss; two_source/direct_fact = growing
  ranking loss
- 10k ladder: candidate +8.46 pp, ranking +6.92 pp article recall;
  controller +60 pp exact-answer; D cases = 73% wrong-value extraction
- Phase 5: P(answerable) ECE 0.045@100k (0.097 excluding 220 template-marker
  cases); P(top1) too weak for routing (gate: no adaptive routing)
- Phase 6: V07 ran carry-OFF (boost 0.0) vs V06 0.35 — pronoun "regression"
  reframed; @25k compat gate best (81.48%, pronoun 98%)

## Access notes

- s600 ssh: `tailscale ssh root@aethera-litellm-s600` — check-mode re-auth
  expires; if ssh stalls, re-auth URL is printed on the attempt.
- Launch pattern: `systemd-run --unit=NAME --working-directory=/root/work/AetherSparse
  --property=Nice=10 /root/work/AetherSparse/.venv/bin/python ...` (nohup dies).
- Sync code first: `rsync -a --exclude='.venv' --exclude='__pycache__'
  --exclude='*.pyc' --exclude='.git' /media/cloud/2982-E16B/work/AetherSparse/
  root@aethera-litellm-s600:/root/work/AetherSparse/`
- Do NOT delete /root/work on the s600 (staged packs).
