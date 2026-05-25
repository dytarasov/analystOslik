import { cn } from "@/lib/utils";

/**
 * Brand mark for «Аналитический Ослик» — the donkey mascot (public/oslik.png).
 * Used in sidebars, the admin header, the login screen and the empty state.
 */
export function DonkeyMark({
  size = 36,
  className,
  rounded = "rounded-xl",
}: {
  size?: number;
  className?: string;
  rounded?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center justify-center overflow-hidden border border-border bg-white shadow-sm",
        rounded,
        className,
      )}
      style={{ width: size, height: size }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src="/oslik.png"
        alt="Аналитический Ослик"
        width={size}
        height={size}
        className="h-full w-full object-contain"
      />
    </span>
  );
}
