import { redirect } from "next/navigation";

// The nurse console is the main view; the classic table lives at /classic.
export default function Home() {
  redirect("/nurse");
}
