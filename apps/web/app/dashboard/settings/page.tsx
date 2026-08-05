"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as React from "react";
import { toast } from "sonner";

import { PageHeader, useMe } from "@/components/shell";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  ErrorState,
  Input,
  Label,
  Modal,
  Select,
  Skeleton,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { Page, Role } from "@/lib/types";

interface Organization {
  id: string;
  name: string;
  slug: string;
  plan: string;
  is_active: boolean;
  settings: Record<string, unknown>;
  created_at: string;
}

interface Member {
  id: string;
  user_id: string;
  email: string;
  full_name: string;
  role: Role;
  created_at: string;
}

const ROLES: Role[] = ["org_admin", "instructor", "reviewer", "support_agent", "learner"];

export default function SettingsPage() {
  const queryClient = useQueryClient();
  const me = useMe();
  const [name, setName] = React.useState("");
  const [inviteOpen, setInviteOpen] = React.useState(false);
  const [invite, setInvite] = React.useState({
    email: "",
    full_name: "",
    role: "instructor" as Role,
    password: "",
  });

  const organization = useQuery({
    queryKey: ["organization"],
    queryFn: () => api.get<Organization>("/api/v1/organizations/current"),
  });

  React.useEffect(() => {
    if (organization.data && !name) setName(organization.data.name);
  }, [organization.data, name]);

  const members = useQuery({
    queryKey: ["members"],
    queryFn: () => api.get<Page<Member>>("/api/v1/organizations/current/members", { limit: 100 }),
  });

  const rename = useMutation({
    mutationFn: () => api.patch<Organization>("/api/v1/organizations/current", { name }),
    onSuccess: () => {
      toast.success("Organization updated");
      void queryClient.invalidateQueries({ queryKey: ["organization"] });
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Update failed"),
  });

  const addMember = useMutation({
    mutationFn: () => api.post<Member>("/api/v1/organizations/current/members", invite),
    onSuccess: () => {
      toast.success("Member added");
      setInviteOpen(false);
      setInvite({ email: "", full_name: "", role: "instructor", password: "" });
      void queryClient.invalidateQueries({ queryKey: ["members"] });
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Could not add member"),
  });

  const changeRole = useMutation({
    mutationFn: ({ id, role }: { id: string; role: Role }) =>
      api.patch<Member>(`/api/v1/organizations/current/members/${id}`, { role }),
    onSuccess: () => {
      toast.success("Role updated");
      void queryClient.invalidateQueries({ queryKey: ["members"] });
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Update failed"),
  });

  const canManage = me.data?.permissions.includes("member:manage");

  return (
    <>
      <PageHeader title="Organization" description="Tenant settings and membership." />

      {organization.error ? (
        <ErrorState error={organization.error} retry={() => organization.refetch()} />
      ) : (
        <div className="grid gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-1">
            <CardHeader>
              <CardTitle>Details</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {organization.isLoading ? (
                <Skeleton className="h-20" />
              ) : (
                <>
                  <div className="space-y-1">
                    <Label htmlFor="org-name">Name</Label>
                    <Input
                      id="org-name"
                      value={name}
                      disabled={!canManage}
                      onChange={(event) => setName(event.target.value)}
                    />
                  </div>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">Slug</span>
                    <code className="rounded bg-muted px-1 py-0.5">{organization.data?.slug}</code>
                  </div>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">Plan</span>
                    <Badge tone="info">{organization.data?.plan}</Badge>
                  </div>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">Created</span>
                    <span>{dateTime(organization.data?.created_at)}</span>
                  </div>
                  {canManage && (
                    <Button
                      className="w-full"
                      loading={rename.isPending}
                      disabled={name === organization.data?.name}
                      onClick={() => rename.mutate()}
                    >
                      Save
                    </Button>
                  )}
                </>
              )}
            </CardContent>
          </Card>

          <Card className="lg:col-span-2">
            <CardHeader className="flex-row items-center justify-between">
              <CardTitle>Members</CardTitle>
              {canManage && (
                <Button size="sm" onClick={() => setInviteOpen(true)}>
                  Add member
                </Button>
              )}
            </CardHeader>
            <CardContent className="p-0">
              {members.isLoading ? (
                <div className="space-y-2 p-4">
                  <Skeleton />
                  <Skeleton />
                </div>
              ) : (
                <Table>
                  <thead>
                    <tr>
                      <Th>Member</Th>
                      <Th>Role</Th>
                      <Th>Joined</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {members.data?.items.map((member) => (
                      <tr key={member.id}>
                        <Td>
                          <p className="font-medium">{member.full_name}</p>
                          <p className="text-2xs text-muted-foreground">{member.email}</p>
                        </Td>
                        <Td>
                          {canManage && member.user_id !== me.data?.user.id ? (
                            <Select
                              aria-label={`Role for ${member.full_name}`}
                              value={member.role}
                              onChange={(event) =>
                                changeRole.mutate({
                                  id: member.id,
                                  role: event.target.value as Role,
                                })
                              }
                            >
                              {ROLES.map((role) => (
                                <option key={role} value={role}>
                                  {role.replace(/_/g, " ")}
                                </option>
                              ))}
                            </Select>
                          ) : (
                            <Badge tone="info">{member.role.replace(/_/g, " ")}</Badge>
                          )}
                        </Td>
                        <Td className="text-muted-foreground">{dateTime(member.created_at)}</Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      <Modal open={inviteOpen} onClose={() => setInviteOpen(false)} title="Add member">
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            addMember.mutate();
          }}
        >
          <div className="space-y-1">
            <Label htmlFor="m-name">Full name</Label>
            <Input
              id="m-name"
              required
              value={invite.full_name}
              onChange={(event) => setInvite({ ...invite, full_name: event.target.value })}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="m-email">Email</Label>
            <Input
              id="m-email"
              type="email"
              required
              value={invite.email}
              onChange={(event) => setInvite({ ...invite, email: event.target.value })}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="m-role">Role</Label>
            <Select
              id="m-role"
              className="w-full"
              value={invite.role}
              onChange={(event) => setInvite({ ...invite, role: event.target.value as Role })}
            >
              {ROLES.map((role) => (
                <option key={role} value={role}>
                  {role.replace(/_/g, " ")}
                </option>
              ))}
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="m-password">Initial password</Label>
            <Input
              id="m-password"
              type="password"
              minLength={10}
              value={invite.password}
              onChange={(event) => setInvite({ ...invite, password: event.target.value })}
            />
            <p className="text-2xs text-muted-foreground">
              There is no mail server in the base setup, so a new user needs an initial password
              set here. Existing users are simply attached to this organization.
            </p>
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={() => setInviteOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={addMember.isPending}>
              Add member
            </Button>
          </div>
        </form>
      </Modal>
    </>
  );
}
