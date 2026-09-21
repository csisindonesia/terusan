import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import {
  AddFilterChip,
  ChoiceList,
  FilterChip,
  summarise,
} from "~/components/filter-chip";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { PERIOD_PATTERN, PeriodFilter } from "~/components/period-filter";
import { api } from "~/lib/api";
import { indicatorLabel } from "~/lib/labels";
import { toggle } from "~/lib/multi";

export type ObservationFilters = {
  indicator?: string[];
  geo?: string;
  geo_type?: string[];
  commodity?: string[];
  period_start?: string;
  period_end?: string;
};

/**
 * Canonical period labels, as Silver writes them. The API rejects anything
 * else, so it is checked here too — a message beside the field beats a 400
 * after a round trip.
 */

const PLACE_TYPES = [
  { value: "country", label: "Countries" },
  // Aggregates are sums of the countries beside them, so a total over
  // everything counts most places more than once.
  { value: "region", label: "Aggregates" },
  { value: "province", label: "Provinces" },
];

type Props = {
  value: ObservationFilters;
  onChange: (next: ObservationFilters) => void;
  onClear: () => void;
};

export function ObservationFilterBar({ value, onChange, onClear }: Props) {
  const indicators = useQuery({
    queryKey: ["indicators"],
    queryFn: () => api.indicators(),
  });

  // Every commodity, not a page of them: the list is a choice list, and one
  // that silently stops at fifty offers a reader a filter they cannot set.
  const commodities = useQuery({
    queryKey: ["commodities", "all"],
    queryFn: () => api.commodities({ limit: 1000 }),
  });

  const byId = useMemo(
    () => new Map((indicators.data?.data ?? []).map((i) => [i.indicator_id, i])),
    [indicators.data],
  );

  const chosenIndicators = value.indicator ?? [];
  const chosenTypes = value.geo_type ?? [];
  const chosenCommodities = value.commodity ?? [];
  const active =
    chosenIndicators.length ||
    chosenTypes.length ||
    chosenCommodities.length ||
    value.geo ||
    value.period_start ||
    value.period_end;

  const periodLabel =
    value.period_start && value.period_end
      ? `${value.period_start}–${value.period_end}`
      : (value.period_start ?? value.period_end);

  return (
    <div className="flex flex-wrap items-center gap-2">
      <FilterChip
        label="Indicator"
        value={summarise(chosenIndicators, (id) =>
          indicatorLabel(byId.get(id) ?? { indicator_id: id }),
        )}
        onClear={() => onChange({ indicator: undefined })}
      >
        <ChoiceList
          // Listed by name: the identifier is a derived code, and a list of
          // forty of them is a list nobody can choose from.
          options={(indicators.data?.data ?? []).map((indicator) => ({
            value: indicator.indicator_id,
            label: indicatorLabel(indicator),
            hint: `${indicator.period_start}–${indicator.period_end}`,
          }))}
          selected={chosenIndicators}
          onToggle={(id) => onChange({ indicator: toggle(chosenIndicators, id) })}
          onClear={() => onChange({ indicator: undefined })}
          empty="No indicators yet."
        />
      </FilterChip>

      <FilterChip
        label="Place type"
        value={summarise(
          chosenTypes,
          (v) => PLACE_TYPES.find((t) => t.value === v)?.label ?? v,
        )}
        onClear={() => onChange({ geo_type: undefined })}
      >
        <ChoiceList
          options={PLACE_TYPES}
          selected={chosenTypes}
          onToggle={(type) => onChange({ geo_type: toggle(chosenTypes, type) })}
          onClear={() => onChange({ geo_type: undefined })}
        />
      </FilterChip>

      <FilterChip
        label="Place"
        value={value.geo}
        onClear={() => onChange({ geo: undefined })}
      >
        <TextFilter
          initial={value.geo ?? ""}
          placeholder="IDN, ID-32, WLD"
          hint="A geography identifier, as the table shows it."
          onApply={(geo) => onChange({ geo: geo || undefined })}
        />
      </FilterChip>

      {/* Only the series that vary by commodity have one, and they name no
          place at all — so this chip and the two above it are alternatives
          rather than filters that narrow each other. */}
      <FilterChip
        label="Commodity"
        value={summarise(chosenCommodities, (name) => name)}
        onClear={() => onChange({ commodity: undefined })}
      >
        <ChoiceList
          options={(commodities.data?.data ?? []).map((commodity) => ({
            value: commodity.name,
            label: commodity.name,
            hint: commodity.units.join(", ") || undefined,
          }))}
          selected={chosenCommodities}
          onToggle={(name) => onChange({ commodity: toggle(chosenCommodities, name) })}
          onClear={() => onChange({ commodity: undefined })}
          empty="No commodity figures yet."
        />
      </FilterChip>

      <FilterChip
        label="Period"
        value={periodLabel}
        onClear={() => onChange({ period_start: undefined, period_end: undefined })}
      >
        <PeriodFilter
          from={value.period_start ?? ""}
          until={value.period_end ?? ""}
          onApply={(period_start, period_end) =>
            onChange({
              period_start: period_start || undefined,
              period_end: period_end || undefined,
            })
          }
        />
      </FilterChip>

      <AddFilterChip>
        <p className="px-2 py-3 text-sm text-muted-foreground">
          Every filter the API supports is already shown. Source and status filters
          arrive with the datasets that need them.
        </p>
      </AddFilterChip>

      {active ? (
        <Button variant="ghost" size="sm" className="h-8" onClick={onClear}>
          Clear all
        </Button>
      ) : null}
    </div>
  );
}

function TextFilter({
  initial,
  placeholder,
  hint,
  onApply,
}: {
  initial: string;
  placeholder: string;
  hint: string;
  onApply: (value: string) => void;
}) {
  const [draft, setDraft] = useState(initial);
  useEffect(() => setDraft(initial), [initial]);

  return (
    <form
      className="grid gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        onApply(draft.trim());
      }}
    >
      <Input
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        placeholder={placeholder}
        autoFocus
      />
      <p className="text-xs text-muted-foreground">{hint}</p>
      <Button type="submit" size="sm">
        Apply
      </Button>
    </form>
  );
}
