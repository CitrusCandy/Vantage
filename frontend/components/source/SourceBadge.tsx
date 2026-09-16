import React from "react";
import { Newspaper, MessageSquare, Twitter, Globe } from "lucide-react";
import { getSourcePlatformMeta } from "@/lib/utils";

interface SourceBadgeProps {
  source: string;
  count?: number;
  className?: string;
}

export const SourceBadge: React.FC<SourceBadgeProps> = ({
  source,
  count,
  className = "",
}) => {
  const meta = getSourcePlatformMeta(source);

  const getIcon = () => {
    switch (meta.iconName) {
      case "news":
        return <Newspaper className="w-3 h-3 text-blue-400" />;
      case "reddit":
        return <MessageSquare className="w-3 h-3 text-orange-400" />;
      case "x":
        return <Twitter className="w-3 h-3 text-zinc-300" />;
      default:
        return <Globe className="w-3 h-3 text-slate-400" />;
    }
  };

  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium border ${meta.badgeBg} ${className}`}
    >
      {getIcon()}
      <span>{meta.name}</span>
      {count !== undefined && (
        <span className="ml-1 opacity-70 font-semibold">{count}</span>
      )}
    </span>
  );
};
