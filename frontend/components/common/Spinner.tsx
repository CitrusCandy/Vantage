import React from "react";
import { cn } from "@/lib/utils";

interface SpinnerProps {
  size?: "sm" | "md" | "lg";
  className?: string;
  label?: string;
}

export const Spinner: React.FC<SpinnerProps> = ({
  size = "md",
  className,
  label,
}) => {
  const sizeMap = {
    sm: "w-4 h-4 border-2",
    md: "w-8 h-8 border-3",
    lg: "w-12 h-12 border-4",
  };

  return (
    <div className="flex flex-col items-center justify-center gap-3 p-4">
      <div
        className={cn(
          "rounded-full border-indigo-500/20 border-t-indigo-500 animate-spin",
          sizeMap[size],
          className
        )}
      />
      {label && <p className="text-sm text-slate-400 font-medium">{label}</p>}
    </div>
  );
};
