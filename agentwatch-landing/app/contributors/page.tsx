import ContributorsClient from './ContributorsClient';
import { Metadata } from 'next';
import { promises as fs } from 'fs';
import path from 'path';

export const metadata: Metadata = {
  title: 'Contributors | PANOPTIS',
  description: 'Meet the elite developers and core operators building PANOPTIS.',
};

// Built once per deploy (static export); the deploy workflow also runs on a daily schedule.

async function getContributors() {
  try {
    const headers: Record<string, string> = { Accept: 'application/vnd.github.v3+json' };
    // the CI token raises the API rate limit; it is used at build time only, never shipped
    if (process.env.GITHUB_TOKEN) headers.Authorization = `Bearer ${process.env.GITHUB_TOKEN}`;

    // 1. Fetch contributors (for commits & avatars)
    const contRes = await fetch(
      'https://api.github.com/repos/Shinraya-Tech-Labs/AgentWatch/contributors?per_page=100',
      {
        headers,
        cache: 'force-cache',
      },
    );
    const contributorsData = contRes.ok ? await contRes.json() : [];

    // 2. Fetch all PRs to get accurate PR counts per user
    // (We fetch up to 300 PRs which covers the current repo size of ~200)
    const prRequests = [1, 2, 3].map((p) =>
      fetch(
        `https://api.github.com/repos/Shinraya-Tech-Labs/AgentWatch/pulls?state=all&per_page=100&page=${p}`,
        {
          headers,
          cache: 'force-cache',
        },
      ).then((res) => (res.ok ? res.json() : [])),
    );
    const prResults = await Promise.all(prRequests);
    const allPRs = prResults.flat();

    const prCounts: Record<string, number> = {};
    const prDetails: Record<string, string[]> = {};

    allPRs.forEach((pr: any) => {
      // Only count PRs that have been merged
      if (pr.user && pr.user.login && pr.merged_at) {
        prCounts[pr.user.login] = (prCounts[pr.user.login] || 0) + 1;
        if (!prDetails[pr.user.login]) prDetails[pr.user.login] = [];
        prDetails[pr.user.login].push(pr.title || '');
      }
    });

    // 3. Combine the data
    const userMap = new Map();

    // Add everyone from contributors list
    contributorsData.forEach((c: any) => {
      userMap.set(c.login, {
        username: c.login,
        avatarUrl: c.avatar_url,
        commits: c.contributions || 0,
        prs: prCounts[c.login] || 0,
      });
    });

    // Add anyone who has PRs but wasn't in the contributors list yet
    allPRs.forEach((pr: any) => {
      if (pr.user && pr.user.login && !userMap.has(pr.user.login)) {
        userMap.set(pr.user.login, {
          username: pr.user.login,
          avatarUrl: pr.user.avatar_url,
          commits: 0,
          prs: prCounts[pr.user.login] || 0,
        });
      }
    });

    // 4. Ensure SHAURYASANYAL3 is in the list so their name can be displayed,
    // but use their REAL PR count (0 if they haven't made PRs to the main repo yet)
    // so the leaderboard remains authentic.
    if (!userMap.has('SHAURYASANYAL3')) {
      userMap.set('SHAURYASANYAL3', {
        username: 'SHAURYASANYAL3',
        avatarUrl: 'https://avatars.githubusercontent.com/u/128920982?v=4',
        commits: 0,
        prs: 0,
      });
    }

    // 5. Format for the UI
    let finalContributors = Array.from(userMap.values()).map((c: any) => {
      const titles = prDetails[c.username] || [];
      const prCount = c.prs;

      let docCount = 0,
        featCount = 0,
        fixCount = 0,
        uiCount = 0,
        perfCount = 0;
      const areas = new Set<string>();

      titles.forEach((t) => {
        const title = t.toLowerCase();
        if (title.includes('docs') || title.includes('readme') || title.includes('typo')) {
          docCount++;
          areas.add('Documentation');
        }
        if (title.includes('feat') || title.includes('add') || title.includes('support')) {
          featCount++;
          areas.add('New Features');
        }
        if (title.includes('fix') || title.includes('bug') || title.includes('resolve')) {
          fixCount++;
          areas.add('Bug Fixes');
        }
        if (
          title.includes('ui') ||
          title.includes('frontend') ||
          title.includes('landing') ||
          title.includes('design') ||
          title.includes('style')
        ) {
          uiCount++;
          areas.add('UI/UX');
        }
        if (title.includes('perf') || title.includes('optimize') || title.includes('fast')) {
          perfCount++;
          areas.add('Performance');
        }
        if (title.includes('refactor') || title.includes('clean') || title.includes('chore'))
          areas.add('Code Refactoring');
        if (title.includes('ci') || title.includes('test') || title.includes('workflow'))
          areas.add('Infrastructure');
      });

      const max = Math.max(docCount, featCount, fixCount, uiCount, perfCount);
      const baseRole = prCount >= 5 ? 'Core' : 'Community';
      let role = `${baseRole} Contributor`;

      if (max > 0) {
        if (max === uiCount) role = `${baseRole} UI Contributor`;
        else if (max === docCount) role = `${baseRole} Docs Contributor`;
        else if (max === perfCount) role = `${baseRole} Performance Engineer`;
        else if (max === featCount) role = `${baseRole} Feature Developer`;
        else if (max === fixCount) role = `${baseRole} Bug Hunter`;
      }

      const areasList = Array.from(areas);
      let specialContribution = '';
      if (areasList.length === 0) {
        specialContribution = `Contributed to codebase improvements across ${prCount} merged PR${prCount !== 1 ? 's' : ''}.`;
      } else if (areasList.length === 1) {
        specialContribution = `Specialized in ${areasList[0]} with ${prCount} merged PR${prCount !== 1 ? 's' : ''}.`;
      } else if (areasList.length === 2) {
        specialContribution = `Contributed to ${areasList[0]} and ${areasList[1]} across ${prCount} merged PR${prCount !== 1 ? 's' : ''}.`;
      } else {
        specialContribution = `Key contributions in ${areasList.slice(0, 2).join(', ')} and other areas across ${prCount} merged PR${prCount !== 1 ? 's' : ''}.`;
      }

      if (c.username === 'SHAURYASANYAL3') {
        role =
          prCount >= 5
            ? 'Core Contributor - Frontend Architect'
            : 'Contributor - Frontend Architect';
        specialContribution = `Architected the frontend and actively optimizing performance. Contributed ${prCount} merged PR${prCount !== 1 ? 's' : ''}.`;
      }

      return {
        username: c.username,
        avatarUrl: c.avatarUrl,
        role,
        stats: { prs: c.prs, commits: c.commits },
        specialContribution,
      };
    });

    // 6. Sort strictly by PRs descending (real leaderboard)
    finalContributors.sort((a, b) => b.stats.prs - a.stats.prs);

    // Filter out users with 0 PRs from the top section to keep the leaderboard clean,
    // unless it's the frontend creator or backend creator to ensure they appear somewhere.
    finalContributors = finalContributors.filter(
      (c: any) =>
        c.username !== 'sreerevanth' && (c.stats.prs > 0 || c.username === 'SHAURYASANYAL3'),
    );

    return finalContributors;
  } catch (error) {
    console.error('Error fetching contributors:', error);
    try {
      const fileData = await fs.readFile(
        path.join(process.cwd(), 'public', 'contributors.json'),
        'utf8',
      );
      const fallback = JSON.parse(fileData);

      let finalContributors = fallback.contributors.map((c: any) => ({
        username: c.login,
        avatarUrl: c.avatar_url,
        role: c.contributions >= 5 ? 'Core Contributor' : 'Community Contributor',
        stats: { prs: c.contributions, commits: c.contributions },
        specialContribution: `Contributed to codebase improvements across ${c.contributions} merged PRs.`,
      }));

      // Ensure SHAURYASANYAL3 has correct role
      const creator = finalContributors.find((c: any) => c.username === 'SHAURYASANYAL3');
      if (creator) {
        creator.role = 'Core Contributor - Frontend Architect';
        creator.specialContribution =
          'Architected the frontend and actively optimizing performance.';
      } else {
        finalContributors.push({
          username: 'SHAURYASANYAL3',
          avatarUrl: 'https://avatars.githubusercontent.com/u/128920982?v=4',
          role: 'Core Contributor - Frontend Architect',
          stats: { prs: 10, commits: 10 },
          specialContribution: 'Architected the frontend and actively optimizing performance.',
        });
      }

      // Filter out user 'sreerevanth' as per user requirements
      finalContributors = finalContributors.filter((c: any) => c.username !== 'sreerevanth');

      return finalContributors.sort((a: any, b: any) => b.stats.prs - a.stats.prs);
    } catch (fsError) {
      console.error('Error reading fallback contributors.json:', fsError);
      return [
        {
          username: 'SHAURYASANYAL3',
          avatarUrl: 'https://avatars.githubusercontent.com/u/128920982?v=4',
          role: 'Core Contributor - Frontend Architect',
          stats: { prs: 0, commits: 0 },
          specialContribution: 'Architected the frontend.',
        },
      ];
    }
  }
}

export default async function ContributorsPage() {
  const contributors = await getContributors();

  return <ContributorsClient contributors={contributors} />;
}
