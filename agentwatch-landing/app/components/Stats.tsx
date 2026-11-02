'use client';

import { useEffect, useRef } from 'react';
import gsap from 'gsap';
import ScrollTrigger from 'gsap/ScrollTrigger';

gsap.registerPlugin(ScrollTrigger);

const stats = [
  {
    value: '1 in 20',
    raw: null,
    label: 'Agent requests fail silently',
  },
  {
    value: '40%',
    raw: 40,
    label: 'Enterprise AI projects cancelled by 2027',
    source: 'Gartner',
  },
  {
    value: '76%',
    raw: 76,
    label: 'Agent deployments fail within 90 days',
  },
];

export default function Stats() {
  const sectionRef = useRef<HTMLElement>(null);
  const numRefs = useRef<(HTMLSpanElement | null)[]>([]);

  useEffect(() => {
    const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (prefersReduced) return;

    const ctx = gsap.context(() => {
      gsap.fromTo(
        '.stat-card',
        { y: 50, opacity: 0 },
        {
          y: 0,
          opacity: 1,
          duration: 0.8,
          stagger: 0.15,
          ease: 'power3.out',
          scrollTrigger: {
            trigger: sectionRef.current,
            start: 'top 80%',
            once: true,
          },
        },
      );

      // Counter animations
      stats.forEach((stat, i) => {
        if (stat.raw === null) return;
        const numEl = numRefs.current[i];
        if (!numEl) return;
        const suffix = stat.value.replace(`${stat.raw}`, '');
        const counter = { val: 0 };

        ScrollTrigger.create({
          trigger: sectionRef.current,
          start: 'top 80%',
          once: true,
          onEnter: () => {
            gsap.to(counter, {
              val: stat.raw!,
              duration: 1.5,
              ease: 'power2.out',
              onUpdate: () => {
                numEl.textContent = `${Math.round(counter.val)}${suffix}`;
              },
            });
          },
        });
      });
    }, sectionRef);

    setTimeout(() => ScrollTrigger.refresh(), 100);
    return () => ctx.revert();
  }, []);

  return (
    <section
      ref={sectionRef}
      className="relative py-16 px-6 dot-grid"
      style={{
        background:
          'radial-gradient(ellipse 62% 70% at 50% 50%, rgba(10,10,10,0.82) 0%, rgba(10,10,10,0.4) 55%, transparent 92%), radial-gradient(ellipse 60% 40% at 50% 50%, rgba(232,255,71,0.04) 0%, transparent 70%)',
      }}
    >
      <div className="max-w-5xl mx-auto grid grid-cols-1 md:grid-cols-3 gap-6">
        {stats.map((stat, i) => (
          <div
            key={i}
            className="stat-card dark-glass relative rounded-xl p-8 border-t-2 border-t-[#e8ff47]"
          >
            <div
              className="font-bold mb-3"
              style={{
                fontFamily: 'var(--font-syne)',
                fontSize: 'clamp(2rem, 4vw, 3rem)',
                color: '#e8ff47',
              }}
            >
              {stat.raw !== null ? (
                <span
                  ref={(el) => {
                    numRefs.current[i] = el;
                  }}
                >
                  {stat.value}
                </span>
              ) : (
                <span>{stat.value}</span>
              )}
            </div>
            <p className="text-[#c0c0c0] text-sm leading-relaxed">
              {stat.label}
              {stat.source && (
                <span className="block mt-1 text-xs text-[#7a7a7a]">Source: {stat.source}</span>
              )}
            </p>
          </div>
        ))}
      </div>
    </section>
  );
}
