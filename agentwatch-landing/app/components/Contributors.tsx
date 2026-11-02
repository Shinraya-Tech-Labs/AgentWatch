'use client';

import { useEffect, useState } from 'react';
import Image from 'next/image';

interface Contributor {
  login: string;
  avatar_url: string;
  html_url: string;
}

export default function Contributors() {
  const [contributors, setContributors] = useState<Contributor[]>([]);

  useEffect(() => {
    fetch('https://api.github.com/repos/sreerevanth/agentwatch/contributors')
      .then((res) => res.json())
      .then((data) => {
        if (Array.isArray(data)) {
          setContributors(data.filter((c: any) => c.login !== 'sreerevanth'));
        }
      })
      .catch((err) => console.error('Error fetching contributors:', err));
  }, []);

  return (
    <section className="relative z-10 py-32 px-6 max-w-7xl mx-auto border-t border-white/5 text-center">
      <h2 className="text-4xl md:text-5xl font-bold tracking-tight mb-4 text-transparent bg-clip-text bg-gradient-to-r from-white to-gray-500">
        Built by the community.
      </h2>
      <p className="text-[#888] font-mono text-xs uppercase tracking-[0.2em] mb-12">
        Join us on GitHub
      </p>

      <div className="flex flex-wrap justify-center gap-4 mb-16">
        {contributors.map((c) => (
          <a
            key={c.login}
            href={c.html_url}
            target="_blank"
            rel="noopener noreferrer"
            className="group relative"
          >
            <div className="w-14 h-14 rounded-full border border-white/10 bg-[#0a0a0a] overflow-hidden hover:border-[#00f0ff] transition-colors relative z-10">
              <Image
                src={c.avatar_url}
                alt=""
                width={56}
                height={56}
                className="w-full h-full object-cover grayscale group-hover:grayscale-0 transition-all"
              />
            </div>
            {/* Tooltip */}
            <div className="absolute -bottom-8 left-1/2 -translate-x-1/2 opacity-0 group-hover:opacity-100 transition-opacity bg-black border border-white/10 text-xs text-white px-2 py-1 rounded whitespace-nowrap z-20 font-mono pointer-events-none">
              {c.login}
            </div>
          </a>
        ))}
      </div>

      <div className="flex justify-center mt-12 relative group inline-block">
        {/* Infinite pulsing aura behind the button */}
        <div
          className="absolute inset-0 bg-gradient-to-r from-[#00f0ff] via-[#e8ff47] to-[#00f0ff] rounded-full blur-[20px] opacity-30 group-hover:opacity-70 group-hover:blur-[30px] transition-all duration-700 animate-pulse"
          style={{ zIndex: -1 }}
        />

        <a
          href="/contributors"
          className="btn-magnetic relative px-10 py-4 rounded-full bg-black/50 backdrop-blur-md border border-[#e8ff47]/50 text-[#e8ff47] font-bold text-sm hover:bg-[#e8ff47] hover:text-black transition-all duration-300 uppercase tracking-widest inline-flex items-center gap-3 overflow-hidden shadow-[0_0_30px_rgba(232,255,71,0.2)] hover:shadow-[0_0_50px_rgba(232,255,71,0.8)]"
          style={{ fontFamily: 'var(--font-jetbrains)' }}
        >
          {/* Shine sweeping effect */}
          <div className="absolute inset-0 -translate-x-full bg-gradient-to-r from-transparent via-white/40 to-transparent group-hover:animate-[shimmer_1.5s_infinite]" />

          <span className="relative z-10">View Hall of Fame</span>
          <svg
            className="w-5 h-5 relative z-10"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M13 10V3L4 14h7v7l9-11h-7z"
            />
          </svg>
        </a>
      </div>
      <style
        dangerouslySetInnerHTML={{
          __html: `
        @keyframes shimmer {
          100% { transform: translateX(100%); }
        }
      `,
        }}
      />
    </section>
  );
}
