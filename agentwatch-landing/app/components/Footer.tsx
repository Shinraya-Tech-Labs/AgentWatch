import { LINKS } from '../site';

export default function Footer() {
  return (
    <footer className="border-t-[3px] border-black bg-black text-[#FFF6DA]">
      <div className="mx-auto grid max-w-6xl gap-6 px-4 py-10 sm:grid-cols-3">
        <div>
          <p className="font-display text-2xl uppercase text-[#FFE45E]">Panoptis</p>
          <p className="mt-2 text-sm">
            Runtime enforcement and tamper-evident records for AI agents. Formerly AgentWatch.
            Apache-2.0.
          </p>
        </div>
        <ul className="space-y-1 font-mono text-sm">
          <li>
            <a className="underline" href={LINKS.readme}>
              Documentation
            </a>
          </li>
          <li>
            <a className="underline" href={LINKS.threatModel}>
              Threat model
            </a>
          </li>
          <li>
            <a className="underline" href={LINKS.claims}>
              Claims and their evidence
            </a>
          </li>
          <li>
            <a className="underline" href={LINKS.pypi}>
              PyPI
            </a>
          </li>
        </ul>
        <ul className="space-y-1 font-mono text-sm">
          <li>
            <a className="underline" href={LINKS.contribute}>
              Contribute
            </a>
          </li>
          <li>
            <a className="underline" href={LINKS.discord}>
              Discord
            </a>
          </li>
          <li>
            <a className="underline" href={LINKS.sponsor}>
              Sponsor
            </a>
          </li>
          <li>
            <a className="underline" href={LINKS.pilot}>
              Pilot with us
            </a>
          </li>
        </ul>
      </div>
    </footer>
  );
}
