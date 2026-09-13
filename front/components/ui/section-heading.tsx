import { cn } from "@/lib/utils";

/** eyebrow + 제목 묶음. 화면마다 같은 위계를 쓰기 위한 최소 래퍼다. */
function SectionHeading({
  eyebrow,
  title,
  as: Heading = "h2",
  className,
}: {
  eyebrow: string;
  title: string;
  as?: "h1" | "h2";
  className?: string;
}) {
  return (
    <div className={cn("min-w-0", className)}>
      <p className="text-[11px] font-bold tracking-wide text-primary uppercase">{eyebrow}</p>
      <Heading
        className={cn(
          "mt-0.5 leading-tight font-bold tracking-tight break-words",
          Heading === "h1" ? "text-xl" : "text-lg",
        )}
      >
        {title}
      </Heading>
    </div>
  );
}

export { SectionHeading };
