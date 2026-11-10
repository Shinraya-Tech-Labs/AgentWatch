import type { Metadata } from 'next';
import { Archivo_Black, Inter, JetBrains_Mono, Syne } from 'next/font/google';

import './globals.css';
import Footer from './components/Footer';
import Navbar from './components/Navbar';

const archivo = Archivo_Black({ variable: '--font-archivo', subsets: ['latin'], weight: '400' });
const syne = Syne({
  variable: '--font-syne',
  subsets: ['latin'],
  weight: ['400', '600', '700', '800'],
});
const inter = Inter({
  variable: '--font-inter',
  subsets: ['latin'],
  weight: ['400', '500', '600', '700'],
});
const jetbrainsMono = JetBrains_Mono({
  variable: '--font-jetbrains',
  subsets: ['latin'],
  weight: ['400', '500', '700'],
});

const SITE = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://shinraya-tech-labs.github.io/AgentWatch';

export const metadata: Metadata = {
  metadataBase: new URL(SITE),
  title: "PANOPTIS — stop an AI agent's tool call before it runs",
  description:
    'Open-source runtime enforcement for AI agents: block dangerous tool calls, hold risky ones for a person, strip prompt injections from tool output, and keep tamper-evident records. pip install panoptis.',
  openGraph: {
    title: 'PANOPTIS',
    description: "Stop an AI agent's tool call before it runs. Open source, local-first.",
    type: 'website',
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`${archivo.variable} ${syne.variable} ${inter.variable} ${jetbrainsMono.variable}`}
    >
      <body className="bg-[#FFF6DA] text-black antialiased">
        <a
          href="#content"
          className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:border-[3px] focus:border-black focus:bg-[#FFE45E] focus:px-3 focus:py-2"
        >
          Skip to content
        </a>
        <Navbar />
        <div id="content">{children}</div>
        <Footer />
      </body>
    </html>
  );
}
