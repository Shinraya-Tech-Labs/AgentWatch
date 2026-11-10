import type { ReactNode } from 'react';

// Neo-brutalist building blocks: thick black borders, hard offset shadows, flat colours.
export const COLORS = {
  yellow: 'bg-[#FFE45E]',
  pink: 'bg-[#FF8FD8]',
  cyan: 'bg-[#7DF9FF]',
  green: 'bg-[#8CFF98]',
  orange: 'bg-[#FFA552]',
  violet: 'bg-[#B8A6FF]',
  white: 'bg-white',
  red: 'bg-[#FF5A5A]',
} as const;
export type Color = keyof typeof COLORS;

export function Box({
  children,
  color = 'white',
  className = '',
  as: Tag = 'div',
}: {
  children: ReactNode;
  color?: Color;
  className?: string;
  as?: 'div' | 'section' | 'article' | 'li';
}) {
  return (
    <Tag
      className={`border-[3px] border-black ${COLORS[color]} shadow-[6px_6px_0_0_#000] ${className}`}
    >
      {children}
    </Tag>
  );
}

export function ButtonLink({
  href,
  children,
  color = 'yellow',
  className = '',
}: {
  href: string;
  children: ReactNode;
  color?: Color;
  className?: string;
}) {
  const external = href.startsWith('http');
  return (
    <a
      href={href}
      {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
      className={`inline-flex items-center justify-center gap-2 border-[3px] border-black ${COLORS[color]} px-5 py-3 font-display text-base uppercase tracking-wide text-black shadow-[4px_4px_0_0_#000] transition-transform hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[6px_6px_0_0_#000] active:translate-x-1 active:translate-y-1 active:shadow-none focus-visible:outline focus-visible:outline-4 focus-visible:outline-offset-2 focus-visible:outline-black ${className}`}
    >
      {children}
    </a>
  );
}

export function SectionTitle({
  kicker,
  children,
  id,
}: {
  kicker: string;
  children: ReactNode;
  id?: string;
}) {
  return (
    <header className="mb-8">
      <p className="mb-3 inline-block border-[3px] border-black bg-black px-3 py-1 font-mono text-xs uppercase tracking-[0.2em] text-[#FFE45E]">
        {kicker}
      </p>
      <h2 id={id} className="font-display text-4xl uppercase leading-[0.95] text-black sm:text-5xl">
        {children}
      </h2>
    </header>
  );
}
