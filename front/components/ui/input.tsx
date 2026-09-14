import * as React from "react";

import { cn } from "@/lib/utils";

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        "w-full min-w-0 bg-transparent py-2.5 text-base text-foreground outline-none placeholder:text-muted-foreground/70",
        className,
      )}
      {...props}
    />
  );
}

export { Input };
