'use client';

import { useEffect } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import gsap from 'gsap';
import ScrollTrigger from 'gsap/ScrollTrigger';

gsap.registerPlugin(ScrollTrigger);

export default function BlogListClient({ posts }: { posts: any[] }) {
  useEffect(() => {
    const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (prefersReduced) return;

    const ctx = gsap.context(() => {
      gsap.fromTo(
        '.blog-header-elem',
        { y: 40, opacity: 0 },
        { y: 0, opacity: 1, duration: 0.8, stagger: 0.1, ease: 'power3.out' },
      );

      gsap.utils.toArray('.blog-card').forEach((card: any) => {
        gsap.fromTo(
          card,
          { y: 50, opacity: 0 },
          {
            y: 0,
            opacity: 1,
            duration: 0.7,
            ease: 'power3.out',
            scrollTrigger: {
              trigger: card,
              start: 'top 85%',
              once: true,
            },
          },
        );
      });
    });

    return () => ctx.revert();
  }, []);

  return (
    <main className="relative min-h-screen bg-[#050505] text-[#ededed] overflow-hidden selection:bg-[#00f0ff]/30 selection:text-[#00f0ff]">
      {/* Background Noise & Grid */}
      <div
        className="absolute inset-0 z-0 pointer-events-none"
        style={{
          backgroundImage:
            'linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px)',
          backgroundSize: '64px 64px',
          backgroundPosition: 'center center',
          maskImage: 'radial-gradient(circle at center, black 20%, transparent 80%)',
          WebkitMaskImage: 'radial-gradient(circle at center, black 20%, transparent 80%)',
        }}
      />
      <div className="absolute top-0 right-0 w-[800px] h-[500px] bg-[#e8ff47] rounded-full blur-[150px] opacity-[0.05] pointer-events-none z-0" />
      <div className="absolute inset-0 bg-[url('/noise.svg')] opacity-[0.05] pointer-events-none mix-blend-overlay z-0" />

      <div className="max-w-[1200px] mx-auto px-6 pt-32 pb-24 relative z-10">
        <header className="mb-16 text-center max-w-[800px] mx-auto">
          <div
            className="blog-header-elem inline-block mb-4 px-3 py-1 rounded-full border border-[#00f0ff]/30 bg-[#00f0ff]/5 text-[#00f0ff] text-xs font-semibold uppercase tracking-widest"
            style={{ fontFamily: 'var(--font-jetbrains)' }}
          >
            Insights & News
          </div>
          <h1
            className="blog-header-elem text-4xl md:text-5xl lg:text-6xl font-extrabold mb-6"
            style={{ fontFamily: 'var(--font-syne)' }}
          >
            The <span className="gradient-text">AgentWatch</span> Blog
          </h1>
          <p className="blog-header-elem text-[#b8b8b8] text-lg md:text-xl">
            Latest industry updates, open-source AI breakthroughs, and in-depth explorations of
            agentic workflows.
          </p>
        </header>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-2 gap-8">
          {posts.map((post, i) => (
            <article
              key={i}
              className="blog-card dark-glass rounded-xl overflow-hidden flex flex-col group border-t-2 border-t-transparent hover:border-t-[#e8ff47] transition-all duration-300"
            >
              <div className="relative h-64 w-full overflow-hidden bg-[#111]">
                <Image
                  src={post.img}
                  alt={post.title}
                  fill
                  className="object-cover transition-transform duration-700 group-hover:scale-105"
                  sizes="(max-width: 768px) 100vw, 50vw"
                  unoptimized // Rely on Unsplash's own CDN optimization
                />
                <div className="absolute inset-0 bg-gradient-to-t from-[#050505] to-transparent opacity-80" />
                <div className="absolute bottom-4 left-6 flex items-center gap-3">
                  <span
                    className="text-[#e8ff47] text-xs font-semibold uppercase tracking-widest px-2 py-1 bg-[#050505]/80 rounded border border-[#e8ff47]/20"
                    style={{ fontFamily: 'var(--font-jetbrains)' }}
                  >
                    {post.date}
                  </span>
                </div>
              </div>

              <div className="p-6 md:p-8 flex-1 flex flex-col">
                <h2
                  className="text-xl md:text-2xl font-bold mb-4 text-[#e5e2e1] group-hover:text-[#00f0ff] transition-colors"
                  style={{ fontFamily: 'var(--font-syne)' }}
                >
                  {post.title}
                </h2>
                <p className="text-[#a0a0a0] text-sm md:text-base leading-relaxed mb-6 flex-1">
                  {post.desc}
                </p>
                <div className="pt-4 mt-auto border-t border-white/5 flex items-center justify-between">
                  <div
                    className="text-xs text-[#888]"
                    style={{ fontFamily: 'var(--font-jetbrains)' }}
                  >
                    Source: <span className="text-[#e5e2e1]">{post.sourceName}</span>
                  </div>
                  <Link
                    href={`/blog/${post.slug}`}
                    className="text-xs font-semibold text-[#00f0ff] hover:text-[#e8ff47] transition-colors flex items-center gap-1"
                    style={{ fontFamily: 'var(--font-jetbrains)' }}
                  >
                    READ MORE
                    <svg
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      className="w-3 h-3"
                    >
                      <path d="M5 12h14M12 5l7 7-7 7" />
                    </svg>
                  </Link>
                </div>
              </div>
            </article>
          ))}
        </div>
      </div>
    </main>
  );
}
