import Link from 'next/link';

import { ADVANCED_VIEWS, Chrome } from '../components/security/SecurityShell';

export default function Advanced() {
  return (
    <Chrome title="Advanced">
      <h1 className="text-xl font-semibold text-zinc-50">Advanced</h1>
      <p className="max-w-2xl text-sm text-zinc-300">
        Policy, MCP and the Evidence Lab: forensic views over recorded runs. They observe and
        explain; enforcement happens on the home screen&apos;s pipeline.
      </p>
      <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {ADVANCED_VIEWS.map((v) => (
          <li key={v.href}>
            <Link href={v.href} className="glass block p-4 hover:bg-white/10">
              <span className="block font-semibold text-zinc-50">{v.label}</span>
              <span className="block text-sm text-zinc-300">{v.hint}</span>
            </Link>
          </li>
        ))}
      </ul>
    </Chrome>
  );
}
