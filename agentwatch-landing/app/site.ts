// Every outbound link of the site, in one place.
export const REPO = 'https://github.com/Shinraya-Tech-Labs/AgentWatch';
export const LINKS = {
  repo: REPO,
  star: `${REPO}/stargazers`,
  contribute: `${REPO}/blob/main/CONTRIBUTING.md`,
  goodFirstIssues: `${REPO}/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22`,
  discord: 'https://discord.gg/UT9uaeY46e',
  sponsor: 'https://github.com/sponsors/sreerevanth',
  pilot: `${REPO}/issues/new?template=pilot_request.yml`,
  readme: `${REPO}#readme`,
  limits: `${REPO}#-what-panoptis-does-not-do`,
  threatModel: `${REPO}/blob/main/docs/THREAT_MODEL.md`,
  claims: `${REPO}/blob/main/docs/CLAIMS.md`,
  pypi: 'https://pypi.org/project/panoptis/',
};

// GitHub Pages serves the site under /<repo>; next/link adds it, plain <video>/<img> do not.
export const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? '';
export const asset = (path: string) => `${BASE_PATH}${path}`;
