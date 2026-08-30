import { db } from "@/lib/db";
import { Role } from "@/generated/prisma/enums";

// Pre-fix TOCTOU code
async function roleFor(email: string): Promise<Role> {
  const adminEmails = (process.env.ADMIN_EMAILS ?? "")
    .split(",")
    .map((e) => e.trim().toLowerCase())
    .filter(Boolean);
  if (adminEmails.includes(email)) return Role.ADMIN;

  const userCount = await db.user.count();
  return userCount === 0 ? Role.ADMIN : Role.STUDENT;
}

export async function POST(request: Request) {
  const { name, email, password } = await request.json();
  const passwordHash = "hashed_password"; // mock
  const role = await roleFor(email);

  try {
    const user = await db.user.create({
      data: { name, email, passwordHash, role },
      select: { id: true, name: true, email: true },
    });
    return { user };
  } catch (err) {
    return { error: "Failed" };
  }
}
