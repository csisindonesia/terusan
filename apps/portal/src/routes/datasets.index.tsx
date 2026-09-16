import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/datasets/")({
  component: DatasetList,
});

function DatasetList() {
  return (
    <section>
      <h1>Datasets</h1>
      <p>The catalog is not wired to the serving layer yet.</p>
    </section>
  );
}
