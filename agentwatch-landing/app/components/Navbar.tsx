import Link from 'next/link';

import { LINKS } from '../site';

const ITEMS = [
  { href: '/#how', label: 'How it works' },
  { href: '/#stops', label: 'What it stops' },
  { href: '/#limits', label: 'Limits' },
  { href: '/contributors', label: 'Contributors' },
];

export default function Navbar() {
  return (
    <header className="sticky top-0 z-40 border-b-[3px] border-black bg-[#FFF6DA]">
      <nav
        aria-label="Main"
        className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-5 gap-y-2 px-4 py-3"
      >
        <Link
          href="/"
          className="mr-auto flex items-center gap-2 font-display text-xl uppercase text-black"
        >
          <span
            aria-hidden
            className="grid h-9 w-9 place-items-center border-[3px] border-black bg-[#FFE45E] shadow-[3px_3px_0_0_#000]"
          >
            P
          </span>
          Panoptis
        </Link>
        <ul className="hidden items-center gap-5 font-mono text-sm uppercase md:flex">
          {ITEMS.map((i) => (
            <li key={i.href}>
              <Link href={i.href} className="text-black underline-offset-4 hover:underline">
                {i.label}
              </Link>
            </li>
          ))}
        </ul>
        <a
          href={LINKS.star}
          target="_blank"
          rel="noopener noreferrer"
          className="border-[3px] border-black bg-black px-3 py-1.5 font-display text-sm uppercase text-[#FFE45E] shadow-[3px_3px_0_0_#FF8FD8] hover:bg-[#222]"
        >
          ★ Star on GitHub
        </a>
      </nav>
    </header>
  );
}
