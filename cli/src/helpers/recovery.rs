// Copyright (c) 2019-2026 Provable Inc.
// This file is part of the snarkOS library.

// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at:

// http://www.apache.org/licenses/LICENSE-2.0

// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

use anyhow::{Result, ensure};
use snarkvm::console::network::{ConsensusVersion, Network};

/// The experimental recovery ledger has no production Aleo ownership-proof or
/// atomic storage adapter. A scheduled activation cannot safely start this node.
pub(crate) fn check_network<N: Network>() -> Result<()> {
    check_heights(N::CONSENSUS_HEIGHT(ConsensusVersion::V23)?, N::CONSENSUS_HEIGHT(ConsensusVersion::V24)?)
}

fn check_heights(preparation: u32, recovery: u32) -> Result<()> {
    ensure!(
        preparation == u32::MAX && recovery == u32::MAX,
        "Missing consensus-integrated recovery backend: V23/V24 must remain unscheduled; the experimental recovery ledger does not verify private Aleo record ownership or commit atomic Aleo migrations"
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn recovery_requires_complete_consensus_backend() {
        assert!(check_heights(u32::MAX, u32::MAX).is_ok());
        for (first, second) in [(30, u32::MAX), (u32::MAX, 40), (30, 40), (0, 0)] {
            assert!(
                check_heights(first, second)
                    .expect_err("incomplete integrations must fail")
                    .to_string()
                    .contains("Missing consensus-integrated recovery backend")
            );
        }
    }
}
