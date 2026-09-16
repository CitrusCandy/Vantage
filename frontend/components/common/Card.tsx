import React from "react";
import { cn } from "@/lib/utils";

interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  hover?: boolean;
  glow?: boolean;
}

export const Card: React.FC<CardProps> = ({
  children,
  hover = false,
  glow = false,
  className,
  ...props
}) => {
  return (
    <div
      className={cn(
        "glass-card rounded-2xl p-5 md:p-6 transition-all duration-250 relative overflow-hidden",
        hover && "glass-card-hover cursor-pointer",
        glow && "border-indigo-500/30 shadow-[0_0_25px_-5px_rgba(99,102,241,0.15)]",
        className
      )}
      {...props}
    >
      {children}
    </div>
  );
};
