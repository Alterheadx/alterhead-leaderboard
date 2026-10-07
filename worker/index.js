// Starts the recompute workflow on a schedule. GitHub's own cron is too unreliable for this.
const WORKFLOW = "https://api.github.com/repos/Alterheadx/alterhead-leaderboard/actions/workflows/recompute.yml/dispatches";

async function dispatch(env) {
  const res = await fetch(WORKFLOW, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "alterhead-leaderboard-cron",
    },
    body: JSON.stringify({ ref: "main" }),
  });
  if (res.status !== 204) {
    throw new Error(`dispatch failed: ${res.status} ${await res.text()}`);
  }
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(dispatch(env));
  },
};
