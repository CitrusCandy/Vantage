import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Vantage News | Multi-Perspective Discourse Intelligence",
  description:
    "Real-time news and public discourse aggregation across Google News, Reddit, and X with MinHash deduplication, HDBSCAN clustering, and traceable LLM perspective synthesis.",
  icons: {
    icon: "/favicon.ico",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark scroll-smooth">
      <body className="min-h-screen bg-background text-slate-100 antialiased selection:bg-indigo-500/30 selection:text-indigo-200">
        <div className="relative min-h-screen flex flex-col justify-between">
          {/* Subtle Ambient Background Gradients */}
          <div className="fixed inset-0 pointer-events-none z-[-1] overflow-hidden">
            <div className="absolute -top-40 left-1/2 -translate-x-1/2 w-[1000px] h-[400px] bg-indigo-600/10 blur-[140px] rounded-full" />
            <div className="absolute top-[40%] -right-40 w-[600px] h-[500px] bg-purple-600/5 blur-[160px] rounded-full" />
            <div className="absolute bottom-0 -left-40 w-[500px] h-[400px] bg-cyan-600/5 blur-[160px] rounded-full" />
          </div>

          <div className="flex-1">{children}</div>
        </div>
      </body>
    </html>
  );
}
