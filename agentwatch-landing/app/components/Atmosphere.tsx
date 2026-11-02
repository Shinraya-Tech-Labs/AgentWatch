'use client';

import { usePathname } from 'next/navigation';
import dynamic from 'next/dynamic';
import { useEffect, useState } from 'react';

const ThreeBackground = dynamic(() => import('./ThreeBackground'), {
  ssr: false,
});
import EdgeTraces from './EdgeTraces';

/**
 * Conditional neural atmosphere.
 *
 * Renders the full Three.js neural canvas + corner edge traces on every
 * route EXCEPT /about. On the About page the background is intentionally
 * a clean black canvas — only the click ripple (mounted separately in
 * layout.tsx) remains as an interactive accent.
 */
export default function Atmosphere() {
  const pathname = usePathname();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    // Defer loading heavy Three.js assets until main thread is idle
    const timer = setTimeout(() => setMounted(true), 500);
    return () => clearTimeout(timer);
  }, []);

  const hidden = pathname?.startsWith('/about');
  if (hidden) return null;
  return (
    <>
      {mounted && <ThreeBackground />}
      <EdgeTraces />
    </>
  );
}
