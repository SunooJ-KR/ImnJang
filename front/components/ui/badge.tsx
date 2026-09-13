import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

// 상태색(초록/주황/빨강)은 쓰지 않는다. 포인트 컬러와 회색 톤으로만 구분한다.
const badgeVariants = cva(
  "inline-flex w-fit shrink-0 items-center gap-1 rounded-sm px-1.5 py-0.5 text-[11px] font-medium whitespace-nowrap",
  {
    variants: {
      variant: {
        default: "bg-neutral-soft text-muted-foreground",
        accent: "bg-primary-soft text-primary-hover",
        neutral: "bg-neutral-soft text-neutral-strong",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

function Badge({
  className,
  variant,
  ...props
}: React.ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return (
    <span data-slot="badge" className={cn(badgeVariants({ variant, className }))} {...props} />
  );
}

export { Badge, badgeVariants };
