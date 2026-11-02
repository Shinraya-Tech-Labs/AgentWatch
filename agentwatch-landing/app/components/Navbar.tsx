'use client';

import { useEffect, useRef, useState } from 'react';
import { usePathname } from 'next/navigation';
import gsap from 'gsap';

const LINKS = [
  { label: 'Features', href: '#features', section: 'features' },
  { label: 'How it works', href: '#how-it-works', section: 'how-it-works' },
  { label: 'Playground', href: '/playground', page: true },
  { label: 'About', href: '/about', page: true },
  { label: 'Contributors', href: '/contributors', page: true },
  { label: 'GitHub', href: 'https://github.com/Shinraya-Tech-Labs/AgentWatch', external: true },
  { label: 'Discord', href: 'https://discord.gg/UT9uaeY46e', external: true },
];

export default function Navbar() {
  const pathname = usePathname();
  const navRef = useRef<HTMLElement>(null);
  const [scrolled, setScrolled] = useState(false);
  const [active, setActive] = useState<string | null>(null);

  useEffect(() => {
    const onScroll = () => {
      setScrolled(window.scrollY > 40);
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    // Run once on mount to catch initial scroll state
    onScroll();

    // Active section tracking via IntersectionObserver
    const ids = LINKS.filter((l) => l.section).map((l) => l.section!) as string[];
    const els = ids
      .map((id) => document.getElementById(id))
      .filter((el): el is HTMLElement => !!el);

    const obs = new IntersectionObserver(
      (entries) => {
        // pick the topmost intersecting section
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (visible?.target?.id) setActive(visible.target.id);
      },
      { rootMargin: '-30% 0px -55% 0px', threshold: 0 },
    );
    els.forEach((el) => obs.observe(el));

    return () => {
      window.removeEventListener('scroll', onScroll);
      obs.disconnect();
    };
  }, []);

  const handleAnchorClick = (e: React.MouseEvent<HTMLAnchorElement>, href: string) => {
    if (!href.startsWith('#')) return;
    // On non-home pages, hash links should route back to the home section
    if (typeof window !== 'undefined' && window.location.pathname !== '/') {
      e.preventDefault();
      window.location.href = `/${href}`;
      return;
    }
    e.preventDefault();
    const el = document.querySelector(href);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  };

  // Magnetic glow for "Get Started"
  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      document.querySelectorAll<HTMLElement>('[data-nav-magnetic]').forEach((el) => {
        const r = el.getBoundingClientRect();
        el.style.setProperty('--mx', `${e.clientX - r.left}px`);
        el.style.setProperty('--my', `${e.clientY - r.top}px`);
      });
    };
    window.addEventListener('pointermove', onMove, { passive: true });
    return () => window.removeEventListener('pointermove', onMove);
  }, []);

  return (
    <nav
      ref={navRef}
      className={`fixed top-0 left-0 right-0 z-[999] transition-all duration-300 ${
        scrolled ? 'bg-[#0a0a0a]/80 backdrop-blur-xl border-b border-white/5' : 'bg-transparent'
      }`}
    >
      <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
        {/* Logo */}
        <a
          href="#"
          className="flex items-center gap-2.5 group"
          onClick={(e) => {
            e.preventDefault();
            window.scrollTo({ top: 0, behavior: 'smooth' });
          }}
        >
          <div className="relative w-8 h-8 flex items-center justify-center">
            <svg
              viewBox="0 0 32 32"
              fill="none"
              className="w-full h-full drop-shadow-[0_0_8px_rgba(232,255,71,0.5)] group-hover:drop-shadow-[0_0_12px_rgba(232,255,71,0.85)] transition-all duration-300"
            >
              <defs>
                <linearGradient id="logo-grad" x1="0%" y1="0%" x2="100%" y2="100%">
                  <stop offset="0%" stopColor="#e8ff47" />
                  <stop offset="100%" stopColor="#bcd20e" />
                </linearGradient>
              </defs>
              <path
                d="M16 3L4 8v8c0 6.627 5.373 12 12 12s12-5.373 12-12V8L16 3z"
                fill="url(#logo-grad)"
                opacity="0.15"
              />
              <path
                d="M16 3L4 8v8c0 6.627 5.373 12 12 12s12-5.373 12-12V8L16 3z"
                stroke="url(#logo-grad)"
                strokeWidth="1.5"
                fill="none"
              />
              <ellipse cx="16" cy="16" rx="5" ry="3.5" stroke="url(#logo-grad)" strokeWidth="1.2" />
              <circle cx="16" cy="16" r="1.8" fill="url(#logo-grad)" />
            </svg>
          </div>
          <span
            className="font-bold text-lg tracking-tight gradient-text"
            style={{ fontFamily: 'var(--font-syne)' }}
          >
            AgentWatch
          </span>
        </a>

        {/* Links */}
        <div className="hidden md:flex items-center gap-7">
          {LINKS.map((item) => {
            if (item.external) {
              return (
                <a
                  key={item.label}
                  href={item.href}
                  target="_blank"
                  rel="noreferrer"
                  className="nav-link text-sm text-[#a8a8a8] hover:text-[#e5e2e1] transition-colors duration-200"
                >
                  {item.label}
                </a>
              );
            }
            if (item.page) {
              // Internal page link (e.g. /about) — use usePathname
              const isActive = pathname === item.href;
              return (
                <a
                  key={item.label}
                  href={item.href}
                  data-active={isActive ? 'true' : 'false'}
                  className="nav-link text-sm text-[#a8a8a8] hover:text-[#e5e2e1] transition-colors duration-200"
                >
                  {item.label}
                </a>
              );
            }
            return (
              <a
                key={item.label}
                href={item.href}
                data-active={active === item.section ? 'true' : 'false'}
                onClick={(e) => handleAnchorClick(e, item.href)}
                className="nav-link text-sm text-[#a8a8a8] hover:text-[#e5e2e1] transition-colors duration-200"
              >
                {item.label}
              </a>
            );
          })}

          <a
            href="https://github.com/sreerevanth/agentwatch"
            target="_blank"
            rel="noreferrer"
            data-nav-magnetic
            className="btn-magnetic relative overflow-hidden px-4 py-2 rounded-lg text-sm font-semibold text-[#0a0a0a] bg-[#e8ff47] hover:bg-[#bcd20e]"
            style={{ boxShadow: '0 0 14px rgba(232,255,71,0.18)' }}
          >
            <span className="relative z-10">Get Started</span>
          </a>
        </div>

        <button
          aria-label="Toggle menu"
          className="md:hidden text-[#888] hover:text-white transition-colors"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            className="w-6 h-6"
          >
            <path strokeLinecap="round" d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        </button>
      </div>
    </nav>
  );
}
