import type { LucideIcon, LucideProps } from "lucide-react";
import {
  ArrowRight, Check, ChevronRight, CircleAlert, Clock, LogOut, Menu, Monitor, Settings, Moon, Plus, RotateCw, SendHorizontal, Sun, X,
} from "lucide-react";

/** App icon set: lucide-react, named imports only (tree-shaken). 20 px, 1.75 stroke, decorative by default. */
function icon(C: LucideIcon) {
  const I = (p: LucideProps) => <C size={20} strokeWidth={1.75} aria-hidden focusable="false" {...p} />;
  I.displayName = `${C.displayName ?? "Icon"}`;
  return I;
}

export const MenuIcon = icon(Menu);
export const PlusIcon = icon(Plus);
export const SendIcon = icon(SendHorizontal);
export const ArrowIcon = icon(ArrowRight);
export const RetryIcon = icon(RotateCw);
export const ChevronIcon = icon(ChevronRight);
export const CloseIcon = icon(X);
export const AlertIcon = icon(CircleAlert);
export const CheckIcon = icon(Check);
export const ClockIcon = icon(Clock);
export const SignOutIcon = icon(LogOut);
export const SystemIcon = icon(Monitor);
export const SunIcon = icon(Sun);
export const MoonIcon = icon(Moon);
export const SettingsIcon = icon(Settings);
