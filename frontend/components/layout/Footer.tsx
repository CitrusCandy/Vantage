import React from "react";
import Link from "next/link";
import { Compass, Shield, Cpu, Network, Sparkles, ExternalLink } from "lucide-react";

export const Footer: React.FC = () => {
  return (
    <footer className="w-full border-t border-white/[0.06] bg-surface/50 mt-20">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-8 mb-8">
          {/* Brand Info */}
          <div className="md:col-span-2 space-y-3">
            <div className="flex items-center gap-2">
              <div className="w-7 h-7 rounded-lg bg-indigo-600 flex items-center justify-center">
                <Compass className="w-4 h-4 text-white" />
              </div>
              <span className="font-bold text-white tracking-tight">VANTAGE NEWS</span>
            </div>
            <p className="text-sm text-slate-400 max-w-md leading-relaxed">
              Real-time multi-perspective discourse intelligence. We aggregate public
              opinions from Google News, Reddit, and X, deduplicate using MinHash/LSH,
              cluster via HDBSCAN, and synthesize structured, traceable viewpoints using AI.
            </p>
          </div>

          {/* Architecture Pillars */}
          <div>
            <h4 className="text-xs font-semibold text-slate-300 uppercase tracking-wider mb-3">
              Pipeline Pillars
            </h4>
            <ul className="space-y-2 text-xs text-slate-400">
              <li className="flex items-center gap-2">
                <Shield className="w-3.5 h-3.5 text-indigo-400" />
                <span>Multi-Source Ingestion & Staging</span>
              </li>
              <li className="flex items-center gap-2">
                <Network className="w-3.5 h-3.5 text-purple-400" />
                <span>MinHash LSH & Bot Filtering</span>
              </li>
              <li className="flex items-center gap-2">
                <Cpu className="w-3.5 h-3.5 text-cyan-400" />
                <span>HDBSCAN Vector Clustering</span>
              </li>
              <li className="flex items-center gap-2">
                <Sparkles className="w-3.5 h-3.5 text-amber-400" />
                <span>Traceable LLM Synthesis</span>
              </li>
            </ul>
          </div>

          {/* Links */}
          <div>
            <h4 className="text-xs font-semibold text-slate-300 uppercase tracking-wider mb-3">
              Repository & Docs
            </h4>
            <ul className="space-y-2 text-xs text-slate-400">
              <li>
                <a
                  href="https://github.com/abhishekchaubey21/Vantage"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:text-white transition-colors flex items-center gap-1"
                >
                  GitHub Repository
                  <ExternalLink className="w-3 h-3 ml-0.5 opacity-60" />
                </a>
              </li>
              <li>
                <Link href="/ops" className="text-indigo-400 hover:text-indigo-300 transition-colors font-medium flex items-center gap-1">
                  Ops Dashboard
                </Link>
              </li>
              <li>
                <span className="text-slate-500">API: FastAPI (Async)</span>
              </li>
              <li>
                <span className="text-slate-500">Frontend: Next.js + Tailwind</span>
              </li>
            </ul>
          </div>
        </div>

        <div className="pt-8 border-t border-white/[0.04] flex flex-col sm:flex-row items-center justify-between text-xs text-slate-500 gap-4">
          <p>© {new Date().getFullYear()} Vantage News. All perspectives objectively synthesized from public discourse.</p>
          <div className="flex items-center gap-4">
            <span className="inline-flex items-center gap-1.5 text-emerald-400">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              Multi-Source Pipeline Active
            </span>
          </div>
        </div>
      </div>
    </footer>
  );
};
