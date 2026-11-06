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

use anyhow::{Context, Result};
use snarkvm::console::network::Network;

/// Checks every signature and aggregation capability required by consensus policy.
pub(crate) fn check_network<N: Network>() -> Result<()> {
    snarkvm::synthesizer::check_recovery_capabilities::<N>()
        .context("Missing or incompatible consensus-required recovery capability")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn native_registry_satisfies_consensus_policy() {
        check_network::<snarkvm::console::network::MainnetV0>().unwrap();
    }

    #[test]
    fn missing_signature_registry_fails_clearly() {
        let policy = snarkvm_recovery::Policy::default();
        let backends = snarkvm_recovery::Backends::default();
        assert_eq!(
            backends.require(&policy.profiles, &policy.aggregates),
            Err(snarkvm_recovery::Error::MissingBackend)
        );
    }
}
