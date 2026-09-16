// Package storage resolves logical data-lake addresses to physical paths.
//
// It mirrors pipelines/src/terusan_pipelines/storage. The two implementations
// must agree: the pipelines write a dataset and the API reads it back, so a
// divergence here is silent data loss rather than a compile error. See
// program.md §45.4.
package storage

// Layer is a top-level directory of the logical storage hierarchy
// (program.md §45.2).
type Layer string

const (
	LayerRaw       Layer = "raw"
	LayerBronze    Layer = "bronze"
	LayerSilver    Layer = "silver"
	LayerGold      Layer = "gold"
	LayerExports   Layer = "exports"
	LayerTemporary Layer = "temporary"
)

// AllLayers lists every layer in hierarchy order.
var AllLayers = []Layer{
	LayerRaw, LayerBronze, LayerSilver, LayerGold, LayerExports, LayerTemporary,
}

// AnalyticalLayers hold the Parquet that queries read.
var AnalyticalLayers = []Layer{LayerBronze, LayerSilver, LayerGold}

// Immutable reports whether a layer must never be rewritten in place. RAW is
// the only non-reproducible lake layer (program.md §45.8).
func (l Layer) Immutable() bool { return l == LayerRaw }

// Disposable reports whether a layer can be deleted wholesale, because its
// contents are rebuildable or bounded by a lifecycle policy.
func (l Layer) Disposable() bool { return l == LayerExports || l == LayerTemporary }

func (l Layer) String() string { return string(l) }
