import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/")({
  component: Home,
});

function Home() {
  return (
    <section>
      <h1>Terusan</h1>
      <p>Research data warehouse and data portal.</p>
    </section>
  );
}
