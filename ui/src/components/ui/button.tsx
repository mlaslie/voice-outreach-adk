import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

// Brand buttons are fully-rounded pills (border-radius: 35px) with
// bold labels; the secondary style is a white pill with a green border and
// green text. Mirrored here.
const buttonVariants = cva(
  "inline-flex items-center justify-center whitespace-nowrap rounded-full text-sm font-bold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "bg-brand text-white shadow-sm hover:bg-brand-dark",
        destructive: "bg-brand-red text-white shadow-sm hover:bg-brand-red-dark",
        outline: "border-2 border-brand bg-white text-brand-dark shadow-sm hover:bg-brand hover:text-white",
        secondary: "bg-mist text-ink shadow-sm hover:bg-rule",
        ghost: "text-ink-soft hover:bg-mist hover:text-ink",
        link: "text-brand underline-offset-4 hover:underline",
        /* Their yellow "Appointments" CTA — the one high-emphasis accent. */
        accent: "bg-brand-yellow text-ink shadow-sm hover:brightness-95",
      },
      size: {
        default: "h-9 px-5 py-2",
        sm: "h-8 px-4 text-xs",
        lg: "h-11 px-8",
        icon: "h-9 w-9",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => {
    return (
      <button
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    );
  }
);
Button.displayName = "Button";

export { Button, buttonVariants };
