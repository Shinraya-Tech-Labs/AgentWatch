'use client';

import { useEffect, useRef } from 'react';
import gsap from 'gsap';
import ScrollTrigger from 'gsap/ScrollTrigger';

gsap.registerPlugin(ScrollTrigger);

const rows = [
  { feature: 'Pre-execution blocking', aw: '✅', langfuse: '❌', phoenix: '❌', dd: '❌' },
  { feature: 'Independent reasoning audit', aw: '✅', langfuse: '❌', phoenix: '❌', dd: '❌' },
  { feature: 'Git-backed rollback', aw: '✅', langfuse: '❌', phoenix: '❌', dd: '❌' },
  { feature: 'Inter-agent causal DAG', aw: '✅', langfuse: '❌', phoenix: '❌', dd: '❌' },
  { feature: 'Cross-session memory', aw: '✅', langfuse: '❌', phoenix: '❌', dd: '❌' },
  { feature: 'Session replay', aw: '✅', langfuse: '❌', phoenix: '✅', dd: '⚠️' },
  { feature: 'Open source', aw: '✅', langfuse: '✅', phoenix: '✅', dd: '❌' },
];

export default function Comparison() {
  const sectionRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (prefersReduced) return;

    const ctx = gsap.context(() => {
      gsap.fromTo(
        '.cmp-title',
        { y: 40, opacity: 0 },
        {
          y: 0,
          opacity: 1,
          duration: 0.8,
          ease: 'power3.out',
          scrollTrigger: { trigger: sectionRef.current, start: 'top 80%', once: true },
        },
      );

      gsap.fromTo(
        '.cmp-row',
        { x: -20, opacity: 0 },
        {
          x: 0,
          opacity: 1,
          duration: 0.6,
          stagger: 0.08,
          ease: 'power3.out',
          scrollTrigger: { trigger: '.cmp-table', start: 'top 85%', once: true },
        },
      );
    }, sectionRef);

    setTimeout(() => ScrollTrigger.refresh(), 100);
    return () => ctx.revert();
  }, []);

  return (
    <section
      ref={sectionRef}
      className="py-16 px-6"
      style={{
        background:
          'radial-gradient(ellipse 65% 70% at 50% 50%, rgba(10,10,10,0.88) 0%, rgba(10,10,10,0.5) 55%, transparent 92%)',
      }}
    >
      <div className="max-w-5xl mx-auto">
        <h2
          className="cmp-title text-center font-bold mb-10"
          style={{
            fontFamily: 'var(--font-syne)',
            fontSize: 'clamp(1.6rem, 3.5vw, 2.5rem)',
          }}
        >
          What nobody else has built.
        </h2>

        <div className="cmp-table dark-glass overflow-x-auto rounded-xl">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/10">
                <th className="text-left px-6 py-4 text-[#a8a8a8] font-medium">Feature</th>
                <th
                  className="px-6 py-4 font-bold"
                  style={{ color: '#e8ff47', fontFamily: 'var(--font-syne)' }}
                >
                  AgentWatch
                </th>
                <th className="px-6 py-4 text-[#a8a8a8] font-medium">Langfuse</th>
                <th className="px-6 py-4 text-[#a8a8a8] font-medium">Phoenix</th>
                <th className="px-6 py-4 text-[#a8a8a8] font-medium">Datadog</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr
                  key={i}
                  className="cmp-row border-b border-white/5 hover:bg-white/[0.02] transition-colors"
                >
                  <td className="px-6 py-4 text-[#c0c0c0]">{row.feature}</td>
                  <td className="px-6 py-4 text-center">
                    <span className="inline-block transition-transform duration-200 hover:scale-110">
                      {row.aw}
                    </span>
                  </td>
                  <td className="px-6 py-4 text-center">{row.langfuse}</td>
                  <td className="px-6 py-4 text-center">{row.phoenix}</td>
                  <td className="px-6 py-4 text-center">{row.dd}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
