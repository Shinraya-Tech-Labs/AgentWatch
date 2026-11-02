import type { Metadata } from 'next';
import { Syne, Inter, JetBrains_Mono } from 'next/font/google';
import './globals.css';
import SmoothScroll from './providers/SmoothScroll';
import Navbar from './components/Navbar';
import Footer from './components/Footer';
import Atmosphere from './components/Atmosphere';
import ClickRipple from './components/ClickRipple';
import CustomCursor from './components/CustomCursor';

const syne = Syne({
  variable: '--font-syne',
  subsets: ['latin'],
  weight: ['400', '600', '700', '800'],
});

const inter = Inter({
  variable: '--font-inter',
  subsets: ['latin'],
  weight: ['300', '400', '500', '600'],
});

const jetbrainsMono = JetBrains_Mono({
  variable: '--font-jetbrains',
  subsets: ['latin'],
  weight: ['400', '500'],
});

export const metadata: Metadata = {
  title: 'AgentWatch — Pre-execution blocking for AI agents',
  description:
    'AgentWatch catches dangerous AI agent actions before they execute. Independent reasoning audit, Git-backed rollback, multi-agent DAG tracing.',
  openGraph: {
    title: 'AgentWatch',
    description: 'Pre-execution blocking for AI agents. Not post-hoc logging.',
    type: 'website',
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${syne.variable} ${inter.variable} ${jetbrainsMono.variable}`}>
      <body className="bg-[#0a0a0a] text-[#e5e2e1] antialiased overflow-x-hidden">
        <SmoothScroll>
          {/* Neural atmosphere — rendered on every route except /about */}
          <Atmosphere />
          {/* Click ripple stays site-wide (including /about) */}
          <ClickRipple />
          <CustomCursor />
          <Navbar />
          {children}
          <Footer />
        </SmoothScroll>
      </body>
    </html>
  );
}
