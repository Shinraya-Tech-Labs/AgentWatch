export default function Footer() {
  return (
    <footer
      className="relative pt-px pb-10"
      style={{
        background: 'linear-gradient(180deg, transparent 0%, rgba(10,10,10,0.7) 40%, #0a0a0a 100%)',
      }}
    >
      {/* Gradient divider */}
      <div
        className="w-full h-px mb-10"
        style={{
          background: 'linear-gradient(90deg, transparent 0%, #e8ff47 50%, transparent 100%)',
        }}
      />

      <div className="max-w-6xl mx-auto px-6 flex flex-col sm:flex-row items-center justify-between gap-4">
        {/* Left */}
        <div className="flex items-center gap-2 text-sm text-[#555]">
          <svg viewBox="0 0 32 32" fill="none" className="w-5 h-5 flex-shrink-0">
            <defs>
              <linearGradient id="footer-grad" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" stopColor="#e8ff47" />
                <stop offset="100%" stopColor="#bcd20e" />
              </linearGradient>
            </defs>
            <path
              d="M16 3L4 8v8c0 6.627 5.373 12 12 12s12-5.373 12-12V8L16 3z"
              stroke="url(#footer-grad)"
              strokeWidth="1.5"
              fill="none"
            />
            <ellipse cx="16" cy="16" rx="5" ry="3.5" stroke="url(#footer-grad)" strokeWidth="1.2" />
            <circle cx="16" cy="16" r="1.8" fill="url(#footer-grad)" />
          </svg>
          <span>
            <span
              className="gradient-text font-semibold"
              style={{ fontFamily: 'var(--font-syne)' }}
            >
              AgentWatch
            </span>{' '}
            · © 2026 Apache 2.0 · Built by sreerevanth
          </span>
        </div>

        {/* Right */}
        <div className="flex items-center gap-6 text-sm text-[#555]">
          <a
            href="https://github.com/sreerevanth/agentwatch"
            target="_blank"
            rel="noreferrer"
            className="hover:text-[#e5e2e1] transition-colors"
          >
            GitHub
          </a>
          <a
            href="https://discord.gg/UT9uaeY46e"
            target="_blank"
            rel="noreferrer"
            className="hover:text-[#e5e2e1] transition-colors"
          >
            Discord
          </a>
          <a
            href="https://docs.agentwatch.dev"
            target="_blank"
            rel="noreferrer"
            className="hover:text-[#e5e2e1] transition-colors"
          >
            Documentation
          </a>
        </div>
      </div>
    </footer>
  );
}
