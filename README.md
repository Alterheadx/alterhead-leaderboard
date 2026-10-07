# PigPonyPocalypse overall ranking

Builds the overall ranking from the per-boss DPS leaderboards on Steam.

Each boss gives up to 100 points: `100 × (your DPS / #1 DPS) ^ 1.5`.
Your overall score is the sum over the four bosses of a difficulty, so Normal and Hard are ranked separately.
When someone sets a new #1 on a boss, everyone else's points for that boss go down on the next update.

Runs on GitHub Actions. Needs the `STEAM_PUBLISHER_KEY` repository secret.

```bash
STEAM_PUBLISHER_KEY=... python3 recompute.py --dry-run
```
