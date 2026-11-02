import Image from 'next/image';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { BLOG_POSTS } from '../data';

// Pre-generate static routes for all blog posts
export function generateStaticParams() {
  return BLOG_POSTS.map((post) => ({
    slug: post.slug,
  }));
}

export default async function BlogPostPage({ params }: { params: Promise<{ slug: string }> }) {
  // Await params to avoid Next.js warnings about synchronous access to params
  const { slug } = await params;

  const post = BLOG_POSTS.find((p) => p.slug === slug);

  if (!post) {
    notFound();
  }

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

      <article className="max-w-[800px] mx-auto px-6 pt-32 pb-24 relative z-10">
        <Link
          href="/blog"
          className="inline-flex items-center gap-2 text-sm text-[#a0a0a0] hover:text-[#e8ff47] transition-colors mb-12"
          style={{ fontFamily: 'var(--font-jetbrains)' }}
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="w-4 h-4"
          >
            <path d="M19 12H5M12 19l-7-7 7-7" />
          </svg>
          Back to Blog
        </Link>

        <header className="mb-12">
          <div
            className="flex items-center gap-4 mb-6 text-xs uppercase tracking-widest font-semibold"
            style={{ fontFamily: 'var(--font-jetbrains)' }}
          >
            <span className="text-[#e8ff47] bg-[#e8ff47]/10 px-3 py-1 rounded border border-[#e8ff47]/20">
              {post.date}
            </span>
            <span className="text-[#a0a0a0]">Source: {post.sourceName}</span>
          </div>

          <h1
            className="text-4xl md:text-5xl lg:text-6xl font-extrabold mb-8 leading-tight"
            style={{ fontFamily: 'var(--font-syne)' }}
          >
            {post.title}
          </h1>

          <div className="relative w-full aspect-[16/9] rounded-xl overflow-hidden mb-12 border border-white/10">
            <Image
              src={post.img}
              alt={post.title}
              fill
              className="object-cover"
              sizes="(max-width: 1024px) 100vw, 800px"
              unoptimized
              priority
            />
          </div>
        </header>

        <div className="prose prose-invert prose-lg max-w-none mb-16">
          {post.content.split('\n\n').map((paragraph, index) => (
            <p key={index} className="text-[#c0c0c0] leading-relaxed mb-6 font-light">
              {paragraph}
            </p>
          ))}
        </div>

        <div className="border-t border-white/10 pt-8 mt-16 flex flex-col md:flex-row md:items-center justify-between gap-6">
          <div>
            <h3
              className="text-[#e5e2e1] font-semibold mb-2"
              style={{ fontFamily: 'var(--font-syne)' }}
            >
              Verify the Source
            </h3>
            <p className="text-[#888] text-sm max-w-md">
              We aggregate crucial insights from across the AI industry. Read the original,
              full-length report from {post.sourceName}.
            </p>
          </div>
          <a
            href={post.sourceUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="btn-magnetic flex-shrink-0 inline-flex items-center gap-2 px-6 py-3 rounded-lg bg-[#e8ff47] text-[#050505] font-bold text-sm hover:bg-[#c4db33] transition-colors"
            style={{ fontFamily: 'var(--font-jetbrains)' }}
          >
            Visit Publisher
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="w-4 h-4"
            >
              <path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6M15 3h6v6M10 14L21 3" />
            </svg>
          </a>
        </div>
      </article>
    </main>
  );
}
