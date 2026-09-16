import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { StanceCategory } from "./types";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatDate(dateString?: string | null): string {
  if (!dateString) return "Never";
  try {
    const date = new Date(dateString);
    return new Intl.DateTimeFormat("en-US", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  } catch {
    return dateString;
  }
}

export function formatTimeAgo(dateString?: string | null): string {
  if (!dateString) return "Just now";
  try {
    const date = new Date(dateString);
    const now = new Date();
    const diffSeconds = Math.floor((now.getTime() - date.getTime()) / 1000);

    if (diffSeconds < 60) return "Just now";
    if (diffSeconds < 3600) return `${Math.floor(diffSeconds / 60)}m ago`;
    if (diffSeconds < 86400) return `${Math.floor(diffSeconds / 3600)}h ago`;
    return `${Math.floor(diffSeconds / 86400)}d ago`;
  } catch {
    return "Recent";
  }
}

export function formatNumber(num: number): string {
  if (num >= 1000000) return `${(num / 1000000).toFixed(1)}M`;
  if (num >= 1000) return `${(num / 1000).toFixed(1)}k`;
  return num.toString();
}

export function classifyStance(type: string): StanceCategory {
  const lower = type.toLowerCase();
  if (lower.includes("support") || lower.includes("advocate") || lower.includes("proponent") || lower.includes("positive") || lower.includes("enthusiast")) {
    return "supportive";
  }
  if (lower.includes("critic") || lower.includes("oppon") || lower.includes("against") || lower.includes("concern") || lower.includes("alarm") || lower.includes("risk")) {
    return "critical";
  }
  if (lower.includes("skeptic") || lower.includes("doubt") || lower.includes("caution") || lower.includes("pragmat")) {
    return "skeptical";
  }
  if (lower.includes("nuanc") || lower.includes("balanc") || lower.includes("complex") || lower.includes("moderate")) {
    return "nuanced";
  }
  if (lower.includes("optimis") || lower.includes("bullish") || lower.includes("opportun")) {
    return "optimistic";
  }
  return "neutral";
}

export function getStanceBadgeStyle(stance: StanceCategory | string): {
  bg: string;
  text: string;
  border: string;
  dot: string;
  label: string;
} {
  const category = typeof stance === "string" ? classifyStance(stance) : stance;
  switch (category) {
    case "supportive":
      return {
        bg: "bg-emerald-500/10",
        text: "text-emerald-400",
        border: "border-emerald-500/30",
        dot: "bg-emerald-400",
        label: "Supportive Stance",
      };
    case "critical":
      return {
        bg: "bg-rose-500/10",
        text: "text-rose-400",
        border: "border-rose-500/30",
        dot: "bg-rose-400",
        label: "Critical / Concern",
      };
    case "skeptical":
      return {
        bg: "bg-amber-500/10",
        text: "text-amber-400",
        border: "border-amber-500/30",
        dot: "bg-amber-400",
        label: "Skeptical / Cautious",
      };
    case "nuanced":
      return {
        bg: "bg-violet-500/10",
        text: "text-violet-400",
        border: "border-violet-500/30",
        dot: "bg-violet-400",
        label: "Nuanced / Structural",
      };
    case "optimistic":
      return {
        bg: "bg-cyan-500/10",
        text: "text-cyan-400",
        border: "border-cyan-500/30",
        dot: "bg-cyan-400",
        label: "Optimistic / Growth",
      };
    default:
      return {
        bg: "bg-slate-500/10",
        text: "text-slate-400",
        border: "border-slate-500/30",
        dot: "bg-slate-400",
        label: "Neutral / Analytical",
      };
  }
}

export function getSourcePlatformMeta(source: string): {
  name: string;
  color: string;
  badgeBg: string;
  iconName: "news" | "reddit" | "x" | "globe";
} {
  const s = source.toLowerCase();
  if (s.includes("google") || s.includes("news")) {
    return {
      name: "Google News",
      color: "text-blue-400",
      badgeBg: "bg-blue-500/10 text-blue-300 border-blue-500/20",
      iconName: "news",
    };
  }
  if (s.includes("reddit")) {
    return {
      name: "Reddit",
      color: "text-orange-400",
      badgeBg: "bg-orange-500/10 text-orange-300 border-orange-500/20",
      iconName: "reddit",
    };
  }
  if (s.includes("x") || s.includes("twitter")) {
    return {
      name: "X (Twitter)",
      color: "text-zinc-200",
      badgeBg: "bg-zinc-700/30 text-zinc-200 border-zinc-600/30",
      iconName: "x",
    };
  }
  return {
    name: "Public Discourse",
    color: "text-slate-400",
    badgeBg: "bg-slate-700/30 text-slate-300 border-slate-600/30",
    iconName: "globe",
  };
}

export function isSafeExternalUrl(url?: string | null): boolean {
  if (!url || typeof url !== "string") return false;
  const trimmed = url.trim().toLowerCase();
  // Strictly enforce http/https protocols to prevent javascript: or data: XSS
  if (!trimmed.startsWith("http://") && !trimmed.startsWith("https://")) {
    return false;
  }
  // Block common local/internal targets
  if (
    trimmed.includes("://localhost") ||
    trimmed.includes("://127.0.0.1") ||
    trimmed.includes("://0.0.0.0") ||
    trimmed.includes("://169.254.169.254") ||
    trimmed.includes("://[::1]")
  ) {
    return false;
  }
  return true;
}
