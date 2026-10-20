import { redirect } from "next/navigation";

// The nurse console is the main view (the admin build redirects / to /admin in middleware).
export default function Home() {
  redirect("/nurse");
}
