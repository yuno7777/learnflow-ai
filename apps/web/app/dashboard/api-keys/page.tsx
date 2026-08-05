"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as React from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/shell";
import {
  Badge,
  Button,
  Card,
  CardContent,
  EmptyState,
  ErrorState,
  Input,
  Label,
  Modal,
  Skeleton,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { dateTime, relativeTime } from "@/lib/format";
import type { ApiKey, Page } from "@/lib/types";

export default function ApiKeysPage() {
  const queryClient = useQueryClient();
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [expiresInDays, setExpiresInDays] = React.useState("");
  const [created, setCreated] = React.useState<ApiKey | null>(null);

  const keys = useQuery({
    queryKey: ["api-keys"],
    queryFn: () => api.get<Page<ApiKey>>("/api/v1/api-keys", { limit: 50 }),
  });

  const create = useMutation({
    mutationFn: () =>
      api.post<ApiKey>("/api/v1/api-keys", {
        name,
        scopes: ["read", "write"],
        expires_in_days: expiresInDays ? Number(expiresInDays) : undefined,
      }),
    onSuccess: (key) => {
      setCreated(key);
      setOpen(false);
      setName("");
      setExpiresInDays("");
      void queryClient.invalidateQueries({ queryKey: ["api-keys"] });
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Create failed"),
  });

  const revoke = useMutation({
    mutationFn: (id: string) => api.delete(`/api/v1/api-keys/${id}`),
    onSuccess: () => {
      toast.success("Key revoked");
      void queryClient.invalidateQueries({ queryKey: ["api-keys"] });
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Revoke failed"),
  });

  return (
    <>
      <PageHeader
        title="API keys"
        description="Machine access for n8n and external systems. Keys are stored hashed — the value is shown exactly once."
        actions={<Button onClick={() => setOpen(true)}>Create key</Button>}
      />

      {keys.error ? (
        <ErrorState error={keys.error} retry={() => keys.refetch()} />
      ) : keys.isLoading ? (
        <Card>
          <CardContent className="space-y-2">
            <Skeleton />
            <Skeleton />
          </CardContent>
        </Card>
      ) : !keys.data?.items.length ? (
        <EmptyState
          title="No API keys"
          description="Create one and put it in LEARNFLOW_API_KEY so n8n can call back into the API."
          action={
            <Button className="mt-2" onClick={() => setOpen(true)}>
              Create key
            </Button>
          }
        />
      ) : (
        <Card>
          <Table>
            <thead>
              <tr>
                <Th>Name</Th>
                <Th>Prefix</Th>
                <Th>Scopes</Th>
                <Th>Last used</Th>
                <Th>Expires</Th>
                <Th>Status</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {keys.data.items.map((key) => (
                <tr key={key.id}>
                  <Td className="font-medium">{key.name}</Td>
                  <Td className="font-mono text-2xs text-muted-foreground">{key.prefix}…</Td>
                  <Td className="text-2xs text-muted-foreground">{key.scopes.join(", ")}</Td>
                  <Td className="text-muted-foreground">{relativeTime(key.last_used_at)}</Td>
                  <Td className="text-muted-foreground">
                    {key.expires_at ? dateTime(key.expires_at) : "never"}
                  </Td>
                  <Td>
                    {key.revoked_at ? (
                      <Badge tone="danger">revoked</Badge>
                    ) : (
                      <Badge tone="success">active</Badge>
                    )}
                  </Td>
                  <Td className="text-right">
                    {!key.revoked_at && (
                      <Button
                        variant="ghost"
                        size="sm"
                        loading={revoke.isPending && revoke.variables === key.id}
                        onClick={() => {
                          if (confirm(`Revoke "${key.name}"? Anything using it stops working.`)) {
                            revoke.mutate(key.id);
                          }
                        }}
                      >
                        Revoke
                      </Button>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      <Modal open={open} onClose={() => setOpen(false)} title="Create API key">
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate();
          }}
        >
          <div className="space-y-1">
            <Label htmlFor="key-name">Name</Label>
            <Input
              id="key-name"
              required
              minLength={2}
              placeholder="n8n automation"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="key-expiry">Expires in days (optional)</Label>
            <Input
              id="key-expiry"
              type="number"
              min={1}
              max={3650}
              value={expiresInDays}
              onChange={(event) => setExpiresInDays(event.target.value)}
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={create.isPending}>
              Create
            </Button>
          </div>
        </form>
      </Modal>

      <Modal open={Boolean(created)} onClose={() => setCreated(null)} title="Copy your API key now">
        <div className="space-y-3">
          <p className="text-xs text-muted-foreground">
            This is the only time the full key is shown. Only its SHA-256 hash is stored.
          </p>
          <code className="block break-all rounded-md bg-muted p-3 font-mono text-xs">
            {created?.key}
          </code>
          <div className="flex justify-end gap-2">
            <Button
              variant="secondary"
              onClick={async () => {
                if (created?.key) {
                  await navigator.clipboard.writeText(created.key);
                  toast.success("Copied to clipboard");
                }
              }}
            >
              Copy
            </Button>
            <Button onClick={() => setCreated(null)}>Done</Button>
          </div>
        </div>
      </Modal>
    </>
  );
}
