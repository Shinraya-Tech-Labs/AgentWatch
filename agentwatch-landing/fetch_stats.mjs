import { execSync } from 'child_process';
import fs from 'fs';

function run(cmd) {
  try {
    return execSync(cmd, { encoding: 'utf8' });
  } catch (e) {
    console.error(`Failed to run: ${cmd}`);
    return '[]';
  }
}

async function main() {
  const repo = 'sreerevanth/agentwatch';

  console.log('Fetching issues...');
  let issuesRaw = run(`gh api "/repos/${repo}/issues?state=all&per_page=100"`);
  let allIssues = JSON.parse(issuesRaw);

  console.log('Fetching contributors...');
  let commitsRaw = run(`gh api "/repos/${repo}/contributors?per_page=100"`);
  let contributors = JSON.parse(commitsRaw);

  const userStats = {};

  contributors.forEach((c) => {
    if (c.type === 'User') {
      userStats[c.login] = {
        username: c.login,
        avatarUrl: c.avatar_url,
        role: 'Contributor',
        stats: { commits: c.contributions, prs: 0, issues: 0 },
        highlights: [],
      };
    }
  });

  allIssues.forEach((issue) => {
    const author = issue.user.login;
    if (!userStats[author]) {
      userStats[author] = {
        username: author,
        avatarUrl: issue.user.avatar_url,
        role: 'Contributor',
        stats: { commits: 0, prs: 0, issues: 0 },
        highlights: [],
      };
    }

    if (issue.pull_request) {
      userStats[author].stats.prs += 1;
      if (userStats[author].highlights.length < 3) {
        userStats[author].highlights.push(`PR #${issue.number}: ${issue.title}`);
      }
    } else {
      userStats[author].stats.issues += 1;
      if (userStats[author].highlights.length < 3 && issue.state === 'closed') {
        userStats[author].highlights.push(`Fixed/Raised Issue #${issue.number}: ${issue.title}`);
      }
    }
  });

  const topContributors = Object.values(userStats).sort(
    (a, b) => b.stats.commits + b.stats.prs * 2 - (a.stats.commits + a.stats.prs * 2),
  );

  if (topContributors.length > 0) {
    topContributors[0].role = 'Creator & Maintainer';
    topContributors[0].specialContribution =
      'Architected AgentWatch and built the core foundation.';
  }

  topContributors.forEach((c) => {
    if (!c.specialContribution)
      c.specialContribution = `Actively contributed to AgentWatch with ${c.stats.commits} commits and ${c.stats.prs} PRs.`;
    if (c.highlights.length === 0)
      c.highlights.push('Consistently improved codebase quality and reliability.');
  });

  console.log('Generated Top Contributors:');
  console.log(JSON.stringify(topContributors.slice(0, 10), null, 2));
}

main();
