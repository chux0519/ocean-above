import type { LucideIcon, LucideProps } from "lucide-react";
import {
  ArrowRight, ArrowUp, ArrowDown, Check, ChevronsUpDown, History, MessageSquare, Plane, SquarePen, Ticket, ChevronRight, ChevronLeft, CircleAlert, CircleUser, Clock, LogOut, Menu, Monitor, Settings, Moon, Plus, RotateCw, Search, Inbox, SendHorizontal, Square, Sun, X,
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
export const AccountIcon = icon(CircleUser);
export const SendUpIcon = icon(ArrowUp);
export const ChatIcon = icon(MessageSquare);
export const TicketIcon = icon(Ticket);
export const HistoryIcon = icon(History);
export const NewChatIcon = icon(SquarePen);
export const UpDownIcon = icon(ChevronsUpDown);
export const PlaneIcon = icon(Plane);
export const SearchIcon = icon(Search);
export const InboxIcon = icon(Inbox);
export const ArrowDownIcon = icon(ArrowDown);
export const BackIcon = icon(ChevronLeft);
export const StopIcon = icon(Square);
