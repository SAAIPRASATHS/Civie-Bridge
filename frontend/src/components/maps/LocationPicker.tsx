/**
 * A map picker for a complaint's location.
 *
 * The coordinates are optional on the backend, so the picker is deliberately
 * additive: it starts empty, the citizen drops a pin by clicking, and a
 * "Remove pin" button returns the form to "no coordinates". OpenStreetMap tiles
 * are used directly - there is no API key in the frontend.
 */

import L from 'leaflet'
import { MapContainer, Marker, TileLayer, useMapEvents } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'

import { Button } from '@/components/ui/button'
import { FieldMessage } from '@/components/ui/field'

export interface LatLng {
  latitude: number
  longitude: number
}

/** The prototype's reference city; the map opens here before a pin is dropped. */
const DEFAULT_CENTER: [number, number] = [13.0827, 80.2707]
const DEFAULT_ZOOM = 12

/**
 * A `divIcon` is used instead of Leaflet's default marker, whose icon URLs do
 * not survive bundling without extra asset plumbing. The marker is styled by
 * `.cb-pin` in `index.css`.
 */
const PIN_ICON = L.divIcon({
  className: 'cb-pin-wrapper',
  html: '<span class="cb-pin"></span>',
  iconSize: [20, 20],
  iconAnchor: [10, 10],
})

function ClickToPlace({ onPlace }: { onPlace: (point: LatLng) => void }) {
  useMapEvents({
    click(event) {
      onPlace({ latitude: event.latlng.lat, longitude: event.latlng.lng })
    },
  })
  return null
}

export function LocationPicker({
  value,
  onChange,
  disabled = false,
}: {
  value: LatLng | null
  onChange: (value: LatLng | null) => void
  disabled?: boolean
}) {
  return (
    <div className="space-y-2">
      <div className="overflow-hidden rounded-lg border border-[var(--border)]">
        <MapContainer
          center={value ? [value.latitude, value.longitude] : DEFAULT_CENTER}
          zoom={value ? 15 : DEFAULT_ZOOM}
          scrollWheelZoom
          className="h-64 w-full"
        >
          <TileLayer
            url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          />
          {disabled ? null : <ClickToPlace onPlace={onChange} />}
          {value ? <Marker position={[value.latitude, value.longitude]} icon={PIN_ICON} /> : null}
        </MapContainer>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <FieldMessage>
          {value
            ? `Pinned at ${value.latitude.toFixed(5)}, ${value.longitude.toFixed(5)}`
            : 'Optional. Click the map to place a pin, or leave the coordinates blank.'}
        </FieldMessage>
        {value ? (
          <Button type="button" variant="ghost" size="sm" onClick={() => onChange(null)}>
            Remove pin
          </Button>
        ) : null}
      </div>
    </div>
  )
}
