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

// Mirrors the server-side rule in RegisterRequest so the user finds out here, not on submit.
const schema = z.object({
  full_name: z.string().min(2, "Enter your name"),
  organization_name: z.string().min(2, "Enter an organization name"),
  email: z.string().email("Enter a valid email address"),
  password: z
    .string()
    .min(10, "At least 10 characters")
    .refine((v) => !/^[a-zA-Z]+$/.test(v) && !/^\d+$/.test(v), "Mix letters with digits or symbols"),
});

type Values = z.infer<typeof schema>;

export default function RegisterPage() {
  const router = useRouter();
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<Values>({ resolver: zodResolver(schema) });

  async function onSubmit(values: Values) {
    try {
      const pair = await api.post<TokenPair>("/api/v1/auth/register", values);
      tokens.save(pair);
      toast.success("Workspace created");
      router.push("/dashboard");
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "Registration failed");
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-6 py-12">
      <div className="w-full max-w-sm">
        <Link href="/" className="text-sm font-semibold tracking-tight">
          LearnFlow AI
        </Link>
        <h1 className="mt-6 text-xl font-semibold">Create a workspace</h1>
        <p className="mt-1 text-xs text-muted-foreground">
          You become the organization administrator.
        </p>

        <Card className="mt-6">
          <CardContent>
            <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
              {(
                [
                  ["full_name", "Your name", "text", "name"],
                  ["organization_name", "Organization", "text", "organization"],
                  ["email", "Work email", "email", "email"],
                  ["password", "Password", "password", "new-password"],
                ] as const
              ).map(([name, label, type, autoComplete]) => (
                <div key={name} className="space-y-1">
                  <Label htmlFor={name}>{label}</Label>
                  <Input id={name} type={type} autoComplete={autoComplete} {...register(name)} />
                  {errors[name] && (
                    <p role="alert" className="text-2xs text-danger">
                      {errors[name]?.message}
                    </p>
                  )}
                </div>
              ))}
              <Button type="submit" className="w-full" loading={isSubmitting}>
                Create workspace
              </Button>
            </form>
          </CardContent>
        </Card>

        <p className="mt-4 text-xs text-muted-foreground">
          Already have an account?{" "}
          <Link href="/login" className="text-primary hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
