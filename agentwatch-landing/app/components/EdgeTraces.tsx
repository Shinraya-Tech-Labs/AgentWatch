'use client';

/**
 * Ambient edge traces — subtle SVG paths in the four corners of the
 * viewport that fade & flow continuously. They reinforce the neural
 * canvas at the EDGES of the layout (where content is sparse) without
 * touching the center content cluster.
 *
 * Pure CSS animation, fixed-positioned, pointer-events none.
 */
export default function EdgeTraces() {
  return (
    <div className="hidden md:block">
      <div
        aria-hidden="true"
        className="fixed inset-0 pointer-events-none z-0"
        style={{ overflow: 'hidden' }}
      >
        {/* Top-left corner trace */}
        <svg
          className="absolute top-0 left-0"
          width="380"
          height="380"
          viewBox="0 0 380 380"
          fill="none"
        >
          <path
            d="M-20 80 C 60 120, 120 60, 200 140 S 320 220, 400 180"
            stroke="rgba(232,255,71,0.12)"
            strokeWidth="1"
            fill="none"
            className="trace-line"
          />
          <path
            d="M 0 200 C 80 240, 160 180, 240 220"
            stroke="rgba(232,255,71,0.08)"
            strokeWidth="1"
            fill="none"
            className="trace-line"
            style={{ animationDelay: '1.2s', animationDuration: '6s' }}
          />
          <circle cx="200" cy="140" r="2" fill="rgba(232,255,71,0.4)" />
          <circle cx="80" cy="100" r="1.5" fill="rgba(232,255,71,0.3)" />
        </svg>

        {/* Top-right corner trace */}
        <svg
          className="absolute top-0 right-0"
          width="380"
          height="380"
          viewBox="0 0 380 380"
          fill="none"
          style={{ transform: 'scaleX(-1)' }}
        >
          <path
            d="M-20 60 C 60 100, 140 40, 220 120 S 340 200, 420 160"
            stroke="rgba(232,255,71,0.1)"
            strokeWidth="1"
            fill="none"
            className="trace-line"
            style={{ animationDelay: '0.6s', animationDuration: '5.4s' }}
          />
          <circle cx="220" cy="120" r="2" fill="rgba(232,255,71,0.35)" />
        </svg>

        {/* Bottom-left corner trace */}
        <svg
          className="absolute bottom-0 left-0"
          width="380"
          height="380"
          viewBox="0 0 380 380"
          fill="none"
          style={{ transform: 'scaleY(-1)' }}
        >
          <path
            d="M-20 100 C 80 60, 160 140, 240 100 S 360 60, 420 120"
            stroke="rgba(232,255,71,0.07)"
            strokeWidth="1"
            fill="none"
            className="trace-line"
            style={{ animationDelay: '2.1s', animationDuration: '7s' }}
          />
          <circle cx="240" cy="100" r="1.5" fill="rgba(232,255,71,0.22)" />
        </svg>

        {/* Bottom-right corner trace */}
        <svg
          className="absolute bottom-0 right-0"
          width="380"
          height="380"
          viewBox="0 0 380 380"
          fill="none"
          style={{ transform: 'scale(-1, -1)' }}
        >
          <path
            d="M-20 80 C 80 40, 160 120, 240 80 S 360 40, 420 100"
            stroke="rgba(232,255,71,0.07)"
            strokeWidth="1"
            fill="none"
            className="trace-line"
            style={{ animationDelay: '1.7s', animationDuration: '6.4s' }}
          />
          <circle cx="240" cy="80" r="1.5" fill="rgba(232,255,71,0.22)" />
        </svg>
      </div>
    </div>
  );
}
