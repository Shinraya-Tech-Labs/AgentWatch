const ITEMS = [
  'PRE-EXECUTION BLOCKING',
  '205 TESTS PASSING',
  'APACHE 2.0',
  'OPEN SOURCE',
  "NSoC 26'",
  'AI AGENT SAFETY',
  '8 ADAPTERS',
];

export default function Marquee() {
  const sequence = [...ITEMS, ...ITEMS, ...ITEMS];

  return (
    <div
      className="relative w-full overflow-hidden"
      style={{
        height: '44px',
        background: '#0d0d0d',
        borderTop: '1px solid rgba(232,255,71,0.25)',
        borderBottom: '1px solid rgba(232,255,71,0.25)',
        zIndex: 1,
        boxShadow: '0 0 24px rgba(232,255,71,0.05), inset 0 0 30px rgba(232,255,71,0.02)',
      }}
    >
      {/* Scrolling content */}
      <div
        className="flex items-center h-full whitespace-nowrap will-change-transform"
        style={{
          animation: 'marquee 24s linear infinite',
          width: 'max-content',
        }}
      >
        {sequence.map((item, i) => (
          <span
            key={i}
            className="px-8 flex items-center gap-8"
            style={{
              fontFamily: 'var(--font-jetbrains)',
              fontSize: '0.75rem',
              letterSpacing: '0.18em',
              color: 'rgba(232,255,71,0.75)',
            }}
          >
            {item}
            <span
              className="inline-block w-1 h-1 rounded-full"
              style={{ background: 'rgba(232,255,71,0.5)' }}
            />
          </span>
        ))}
      </div>

      {/* Traveling shimmer overlay */}
      <div
        className="absolute inset-y-0 pointer-events-none"
        style={{
          width: '20%',
          background: 'linear-gradient(90deg, transparent, rgba(232,255,71,0.10), transparent)',
          animation: 'scan-sweep 6s linear infinite',
        }}
      />

      {/* Edge fades (left/right) so the loop never looks like a hard cut */}
      <div
        className="absolute inset-y-0 left-0 w-24 pointer-events-none"
        style={{
          background:
            'linear-gradient(90deg, #0d0d0d 0%, rgba(13,13,13,0.6) 50%, transparent 100%)',
        }}
      />
      <div
        className="absolute inset-y-0 right-0 w-24 pointer-events-none"
        style={{
          background:
            'linear-gradient(90deg, transparent 0%, rgba(13,13,13,0.6) 50%, #0d0d0d 100%)',
        }}
      />
    </div>
  );
}
