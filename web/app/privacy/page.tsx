import type { Metadata } from "next";
import { LegalPage } from "@/components/LegalPage";

export const metadata: Metadata = { title: "Privacy · reScrub" };

export default function PrivacyPage() {
  return (
    <LegalPage title="Privacy and upload handling" updated="October 18, 2026">
      <section>
        <h2>The short version</h2>
        <p>
          Use simulated or properly de-identified footage you are allowed to share. Recordings you
          analyze are processed on reScrub’s own servers, are visible only to your account, and
          can be deleted at any time.
        </p>
      </section>

      <section>
        <h2>What happens to your data</h2>
        <ul>
          <li>
            <strong>Choosing a file.</strong> The recording stays in your browser. The preview plays
            from a temporary local link that is released when you replace or remove the file, start a
            new review, or close the page.
          </li>
          <li>
            <strong>Demonstration.</strong> Uses a synthetic clip served by this website. Nothing is
            uploaded. Your Confirm and Dismiss choices exist only in this tab and disappear when you
            reload.
          </li>
          <li>
            <strong>Analyze recording.</strong> Requires sign-in. The recording and the session
            details you typed are uploaded to the reScrub servers (hosted on Vultr). reScrub’s
            computer-vision software labels people anonymously (“Person 1”, “Person 2”), tracks arm
            and hand positions, and proposes findings. Tracking data, findings, and your review
            decisions are stored in a Tiger Cloud database (TimescaleDB on PostgreSQL); the labeled video is stored
            in private Vultr Object Storage. Only your account can open them.
          </li>
          <li>
            <strong>Final report.</strong> Only when you choose <em>Write report with Gemini</em>: the
            text of the findings (titles, descriptions, evidence labels and times, uncertainty), your
            Confirm and Dismiss decisions, summary counts, the recording length and the procedure
            context you typed are sent to Google’s Gemini API, which writes the report. The video,
            frames, file name, session name and your account details are never sent. The report is
            stored with the recording and deleted with it.
          </li>
          <li>
            <strong>Export review PDF and Export incident JSON.</strong> Create a file on your device
            (through your browser’s print dialog for the PDF). Nothing is sent anywhere.
          </li>
          <li>
            <strong>Accounts.</strong> Accounts are created by an administrator. We store your email,
            a one-way hash of your password (never the password itself), when you last signed in,
            and a sign-in cookie that is required for the site to work. There are no analytics or
            advertising cookies.
          </li>
        </ul>
      </section>

      <section>
        <h2>Outside services</h2>
        <p>
          Vultr hosts the servers and the video storage, and Tiger Data hosts the database. When you
          ask for a final report, Google processes the finding text described above under the
          Gemini API terms. Footage is never sent to Google or to any other company or AI service.
        </p>
      </section>

      <section>
        <h2>Storage and deletion</h2>
        <p>
          The uploaded recording is deleted as soon as its labeled video has been made. The labeled
          video, tracking data, findings, and your decisions are kept until you or an administrator
          delete them. <strong>Delete recording</strong> in a review removes all of these; deleting
          an account removes the account and every recording it owns.
        </p>
        <p>
          Only reScrub administrators, who operate the servers, can access stored data directly, and
          only to run and support the service.
        </p>
      </section>

      <section>
        <h2>Contact</h2>
        <p>
          For privacy questions, deletion of your account, or a copy of your data, contact the
          administrator who created your account.
        </p>
      </section>
    </LegalPage>
  );
}
