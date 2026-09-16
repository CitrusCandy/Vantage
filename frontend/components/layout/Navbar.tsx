"use client";

import React, { useState, useEffect } from "react";
import Link from "next/link";
import {
  Compass,
  TrendingUp,
  Sparkles,
  PlusCircle,
  Activity,
  Layers,
  Github,
  RefreshCw,
} from "lucide-react";
import { Button } from "../common/Button";
import { Badge } from "../common/Badge";
import { getWorkerStatus } from "@/lib/api";
import { WorkerStatus } from "@/lib/types";

interface NavbarProps {
  onOpenCreateModal?: () => void;
}

export const Navbar: React.FC<NavbarProps> = ({ onOpenCreateModal }) => {
  const [workerStatus, setWorkerStatus] = useState<WorkerStatus | null>(null);

  useEffect(() => {
    getWorkerStatus()
      .then(setWorkerStatus)
      .catch(() => null);
  }, []);

  return (
    <header className="sticky top-0 z-40 w-full border-b border-white/[0.06] bg-background/80 backdrop-blur-xl">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between gap-4">
        {/* Logo */}
        <Link href="/" className="flex items-center gap-3 group">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-indigo-500 via-primary-600 to-purple-600 flex items-center justify-center shadow-lg shadow-indigo-500/25 border border-indigo-400/40 group-hover:scale-105 transition-transform duration-200">
            <Compass className="w-5 h-5 text-white" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-extrabold text-lg sm:text-xl tracking-tight text-white font-sans">
                VANTAGE
              </span>
              <span className="text-xs font-semibold px-1.5 py-0.5 rounded bg-indigo-500/20 text-indigo-400 border border-indigo-500/30 uppercase tracking-wider">
                News
              </span>
            </div>
            <p className="text-[10px] text-slate-400 font-medium tracking-wide hidden sm:block">
              Multi-Perspective Discourse Intelligence
            </p>
          </div>
        </Link>

        {/* Center Nav Links */}
        <nav className="hidden md:flex items-center gap-1 bg-surface-light/40 border border-white/[0.05] p-1 rounded-xl">
          <Link
            href="/"
            className="px-3.5 py-1.5 text-xs font-medium text-slate-300 hover:text-white hover:bg-white/[0.06] rounded-lg transition-colors flex items-center gap-1.5"
          >
            <TrendingUp className="w-3.5 h-3.5 text-indigo-400" />
            Trending Feed
          </Link>
          <Link
            href="/#topics"
            className="px-3.5 py-1.5 text-xs font-medium text-slate-300 hover:text-white hover:bg-white/[0.06] rounded-lg transition-colors flex items-center gap-1.5"
          >
            <Layers className="w-3.5 h-3.5 text-slate-400" />
            All Topics
          </Link>
          <Link
            href="/ops"
            className="px-3.5 py-1.5 text-xs font-medium text-slate-300 hover:text-white hover:bg-white/[0.06] rounded-lg transition-colors flex items-center gap-1.5"
          >
            <Activity className="w-3.5 h-3.5 text-emerald-400" />
            Operations
          </Link>
        </nav>

        {/* Right Action Items */}
        <div className="flex items-center gap-3">
          {/* Worker Status Pill */}
          <div className="hidden lg:flex items-center gap-2 px-3 py-1.5 rounded-xl bg-surface-light/60 border border-surface-border text-xs text-slate-300">
            <span
              className={`w-2 h-2 rounded-full ${
                workerStatus?.is_running
                  ? "bg-emerald-400 animate-pulse"
                  : "bg-indigo-400"
              }`}
            />
            <span className="text-slate-400">Worker:</span>
            <span className="font-medium text-slate-200">
              {workerStatus?.is_running ? "Running" : "Ready"}
            </span>
          </div>

          {/* New Topic Button */}
          {onOpenCreateModal && (
            <Button
              size="sm"
              variant="primary"
              icon={<PlusCircle className="w-4 h-4" />}
              onClick={onOpenCreateModal}
            >
              Analyze Topic
            </Button>
          )}

          <a
            href="https://github.com/abhishekchaubey21/Vantage"
            target="_blank"
            rel="noopener noreferrer"
            className="p-2 rounded-xl text-slate-400 hover:text-white hover:bg-surface-light border border-transparent hover:border-surface-border transition-colors"
            title="View on GitHub"
          >
            <Github className="w-5 h-5" />
          </a>
        </div>
      </div>
    </header>
  );
};
