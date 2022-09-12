/*
This script configures Nexus so that Cachito can stage Rubygems content for the Cachito request.

This script creates a Rubygems hosted repository and a raw repository to be used by a Cachito
request to fetch Ruby content

No permissions are configured since it is expected that Cachito's Nexus service account has access
to use all Ruby related repositories managed by the Nexus instance.
 */
import groovy.json.JsonSlurper
import groovy.transform.Field
import org.slf4j.Logger
import org.slf4j.LoggerFactory
import org.sonatype.nexus.repository.config.Configuration
import org.sonatype.nexus.repository.config.WritePolicy


// Scope logger to the script using @Field
@Field final Logger logger = LoggerFactory.getLogger('cachito');


def createHostedRubyGemsRepo(String name) {
    WritePolicy writePolicy = WritePolicy.ALLOW_ONCE
    Boolean strictContentValidation = true
    String blobStoreName = "cachito-rubygems"
    // repository is an object that is injected by Nexus when the script is executed
    if(repository.repositoryManager.exists(name)) {
        logger.info("Modifying the hosted repository ${name}")
        Configuration hostedRepoConfig = repository.repositoryManager.get(name).configuration
        def storage = hostedRepoConfig.attributes('storage')
        storage.set('strictContentTypeValidation', strictContentValidation)
        storage.set('writePolicy', writePolicy)
        repository.repositoryManager.update(hostedRepoConfig)
    }
    else {
        logger.info("Creating the hosted Rubygems repository ${name}")
        repository.createRubygemsHosted(name, blobStoreName, strictContentValidation, writePolicy)
    }
}


request = new JsonSlurper().parseText(args)
['rubygems_repository_name'].each { param ->
    assert request.get(param): "The ${param} parameter is required"
}

createHostedRubyGemsRepo(request.rubygems_repository_name)

return 'The repositories were created successfully'
