"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { Button, Card, CardContent, Input, Label } from "@/components/ui";
import { ApiError, api, tokens } from "@/lib/api";
import type { TokenPair } from "@/lib/types";

const schema = z.object({
  email: z.string().email("Enter a valid email address"),
  password: z.string().min(1, "Password is required"),
});

type Values = z.infer<typeof schema>;

export default function LoginPage() {
  const router = useRouter();
  const {
    register,
    handleSubmit,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<Values>({ resolver: zodResolver(schema) });

  async function onSubmit(values: Values) {
    try {
      const pair = await api.post<TokenPair>("/api/v1/auth/login", values);
      tokens.save(pair);
      toast.success(`Signed in to ${pair.memberships[0]?.organization_name ?? "your workspace"}`);
      // Learners get the portal; the admin dashboard would 403 on every panel.
      router.push(pair.role === "learner" ? "/learn" : "/dashboard");
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "Sign in failed");
    }
  }

  function useDemoCredentials() {
    setValue("email", "admin@learnflow.dev");
    setValue("password", "LearnFlow!2026");
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-sm">
        <Link href="/" className="text-sm font-semibold tracking-tight">
          LearnFlow AI
        </Link>
        <h1 className="mt-6 text-xl font-semibold">Sign in</h1>
        <p className="mt-1 text-xs text-muted-foreground">
          Use your organization account.
        </p>

        <Card className="mt-6">
          <CardContent>
            <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
              <div className="space-y-1">
                <Label htmlFor="email">Email</Label>
                <Input id="email" type="email" autoComplete="email" {...register("email")} />
                {errors.email && (
                  <p role="alert" className="text-2xs text-danger">
                    {errors.email.message}
                  </p>
                )}
              </div>
              <div className="space-y-1">
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  {...register("password")}
                />
                {errors.password && (
                  <p role="alert" className="text-2xs text-danger">
                    {errors.password.message}
                  </p>
                )}
              </div>
              <Button type="submit" className="w-full" loading={isSubmitting}>
                Sign in
              </Button>
            </form>

            <div className="mt-4 border-t border-border pt-4">
              <p className="text-2xs text-muted-foreground">
                Running the local demo seed? Fill in the demo administrator.
              </p>
              <Button variant="ghost" size="sm" className="mt-1 px-0" onClick={useDemoCredentials}>
                Use demo credentials
              </Button>
            </div>
          </CardContent>
        </Card>

        <p className="mt-4 text-xs text-muted-foreground">
          No workspace yet?{" "}
          <Link href="/register" className="text-primary hover:underline">
            Create one
          </Link>
        </p>
      </div>
    </div>
  );
}
